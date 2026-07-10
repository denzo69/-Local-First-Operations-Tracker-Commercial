from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditLog, Customer, Job, JobItem, JobStatus, Product
from app.services.audit_service import log_audit_event
from app.services.receipt_number_service import format_receipt_number
from app.services.settings_service import get_app_settings
from app.template_context import templates

router = APIRouter(prefix="/jobs", tags=["jobs"])
settings = get_settings()

DEFAULT_JOB_STATUSES = [
    {
        "name": "Received",
        "sort_order": 10,
        "is_final": False,
        "is_ready_state": False,
        "is_packed_state": False,
    },
    {
        "name": "In progress",
        "sort_order": 20,
        "is_final": False,
        "is_ready_state": False,
        "is_packed_state": False,
    },
    {
        "name": "Ready for pickup",
        "sort_order": 30,
        "is_final": False,
        "is_ready_state": True,
        "is_packed_state": True,
    },
    {
        "name": "Picked up",
        "sort_order": 40,
        "is_final": True,
        "is_ready_state": False,
        "is_packed_state": False,
    },
]


def ensure_default_job_statuses(db: Session) -> list[JobStatus]:
    for status_data in DEFAULT_JOB_STATUSES:
        status = db.query(JobStatus).filter(JobStatus.name == status_data["name"]).first()
        if status is None:
            db.add(JobStatus(**status_data))
        else:
            for key, value in status_data.items():
                setattr(status, key, value)

    db.commit()
    return (
        db.query(JobStatus)
        .filter(JobStatus.is_active.is_(True))
        .order_by(JobStatus.sort_order.asc(), JobStatus.name.asc())
        .all()
    )


def get_received_status(db: Session) -> JobStatus:
    statuses = ensure_default_job_statuses(db)
    return next(status for status in statuses if status.name == "Received")


def ensure_receipt_number(db: Session, job: Job) -> str:
    if job.receipt_number:
        return job.receipt_number

    app_settings = get_app_settings(db)
    sequence = job.id
    year = job.created_at.year if job.created_at else date.today().year
    job.receipt_number = format_receipt_number(
        year,
        sequence,
        prefix=app_settings.get("receipt_prefix", ""),
    )
    db.commit()
    db.refresh(job)
    return job.receipt_number


def parse_optional_date(value: str) -> date | None:
    if not value.strip():
        return None
    return date.fromisoformat(value)


@router.get("", response_class=HTMLResponse)
def list_jobs(
    request: Request,
    view: str = Query("active"),
    q: str = Query(""),
    db: Session = Depends(get_db),
):
    allowed_views = {"active", "ready", "history", "all"}
    if view not in allowed_views:
        view = "active"

    ensure_default_job_statuses(db)
    query = db.query(Job).join(Job.status, isouter=True)

    if view == "active":
        query = query.filter(Job.status_id.is_(None) | Job.status.has(is_final=False))
    elif view == "ready":
        query = query.filter(Job.status.has(is_ready_state=True))
    elif view == "history":
        query = query.filter(Job.status.has(is_final=True))

    search = q.strip()
    if search:
        query = query.filter(
            Job.title.ilike(f"%{search}%")
            | Job.receipt_number.ilike(f"%{search}%")
            | Job.description.ilike(f"%{search}%")
            | Job.notes.ilike(f"%{search}%")
            | Job.customer.has(Customer.name.ilike(f"%{search}%"))
        )

    jobs = query.order_by(Job.created_at.desc()).all()
    return templates.TemplateResponse(
        "jobs/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "jobs",
            "view": view,
            "q": q,
            "jobs": jobs,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_job(request: Request, db: Session = Depends(get_db)):
    customers = db.query(Customer).order_by(Customer.name.asc()).all()
    statuses = ensure_default_job_statuses(db)
    return templates.TemplateResponse(
        "jobs/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "jobs",
            "customers": customers,
            "statuses": statuses,
            "job": None,
            "form_action": "/jobs",
            "page_title": "New job",
        },
    )


@router.post("")
def create_job(
    title: str = Form(...),
    customer_id: int | None = Form(None),
    description: str = Form(""),
    arrival_date: str = Form(""),
    requested_pickup_date: str = Form(""),
    priority: str = Form("normal"),
    status_id: int | None = Form(None),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    if not title.strip():
        raise HTTPException(status_code=400, detail="Job title is required")

    customer = db.get(Customer, customer_id) if customer_id else None
    if customer_id and customer is None:
        raise HTTPException(status_code=400, detail="Selected customer was not found")

    try:
        parsed_arrival_date = parse_optional_date(arrival_date)
        parsed_pickup_date = parse_optional_date(requested_pickup_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format") from exc

    status = db.get(JobStatus, status_id) if status_id else get_received_status(db)
    if status_id and status is None:
        raise HTTPException(status_code=400, detail="Selected status was not found")

    job = Job(
        title=title.strip(),
        customer=customer,
        description=description.strip() or None,
        arrival_date=parsed_arrival_date,
        requested_pickup_date=parsed_pickup_date,
        status=status,
        priority=priority.strip() or "normal",
        notes=notes.strip() or None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    ensure_receipt_number(db, job)
    log_audit_event(
        db,
        event_type="job_created",
        entity_type="job",
        entity_id=job.id,
        description=f"Job created with status {job.status.name if job.status else 'Received'}.",
    )
    db.commit()

    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)


@router.get("/{job_id}/edit", response_class=HTMLResponse)
def edit_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    customers = db.query(Customer).order_by(Customer.name.asc()).all()
    statuses = ensure_default_job_statuses(db)
    return templates.TemplateResponse(
        "jobs/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "jobs",
            "customers": customers,
            "statuses": statuses,
            "job": job,
            "form_action": f"/jobs/{job.id}",
            "page_title": "Edit job",
        },
    )


@router.post("/{job_id}")
def update_job(
    job_id: int,
    title: str = Form(...),
    customer_id: int | None = Form(None),
    description: str = Form(""),
    arrival_date: str = Form(""),
    requested_pickup_date: str = Form(""),
    priority: str = Form("normal"),
    status_id: int | None = Form(None),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not title.strip():
        raise HTTPException(status_code=400, detail="Job title is required")

    customer = db.get(Customer, customer_id) if customer_id else None
    if customer_id and customer is None:
        raise HTTPException(status_code=400, detail="Selected customer was not found")

    status = db.get(JobStatus, status_id) if status_id else None
    if status_id and status is None:
        raise HTTPException(status_code=400, detail="Selected status was not found")

    try:
        job.arrival_date = parse_optional_date(arrival_date)
        job.requested_pickup_date = parse_optional_date(requested_pickup_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format") from exc

    job.title = title.strip()
    job.customer = customer
    job.description = description.strip() or None
    job.priority = priority.strip() or "normal"
    job.notes = notes.strip() or None
    if status is not None:
        job.status = status

    log_audit_event(
        db,
        event_type="job_updated",
        entity_type="job",
        entity_id=job.id,
        description="Job details updated.",
    )
    db.commit()
    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)


@router.get("/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    statuses = ensure_default_job_statuses(db)
    products = (
        db.query(Product)
        .filter(Product.is_active.is_(True))
        .order_by(Product.name.asc())
        .all()
    )
    audit_events = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "job", AuditLog.entity_id == job.id)
        .order_by(AuditLog.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        "jobs/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "jobs",
            "job": job,
            "statuses": statuses,
            "products": products,
            "audit_events": audit_events,
            "job_total": sum(item.line_total or 0 for item in job.items),
        },
    )


@router.get("/{job_id}/receipt", response_class=HTMLResponse)
def job_receipt(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    ensure_receipt_number(db, job)
    app_settings = get_app_settings(db)

    return templates.TemplateResponse(
        "jobs/receipt.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "jobs",
            "job": job,
            "company": app_settings,
            "job_total": sum(item.line_total or 0 for item in job.items),
        },
    )


@router.post("/{job_id}/items")
def add_job_item(
    job_id: int,
    product_id: int | None = Form(None),
    description: str = Form(""),
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    vat_percent: str = Form("24"),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    product = db.get(Product, product_id) if product_id else None
    parsed_quantity = float(str(quantity or "1").replace(",", "."))
    parsed_unit_price = float(str(unit_price or "0").replace(",", "."))
    parsed_vat_percent = float(str(vat_percent or "24").replace(",", "."))
    item_description = description.strip()

    if product is not None and not item_description:
        item_description = product.name
        parsed_unit_price = product.unit_price
        parsed_vat_percent = product.vat_percent

    if not item_description:
        raise HTTPException(status_code=400, detail="Item description is required")

    line_total = round(parsed_quantity * parsed_unit_price, 2)
    item = JobItem(
        job=job,
        product=product,
        description=item_description,
        quantity=parsed_quantity,
        unit_price=parsed_unit_price,
        vat_percent=parsed_vat_percent,
        line_total=line_total,
    )
    db.add(item)
    log_audit_event(
        db,
        event_type="job_item_added",
        entity_type="job",
        entity_id=job.id,
        description=f"Item added: {item_description} x {parsed_quantity}.",
    )
    db.commit()
    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)


@router.post("/{job_id}/items/{item_id}/delete")
def delete_job_item(job_id: int, item_id: int, db: Session = Depends(get_db)):
    item = db.get(JobItem, item_id)
    if item is None or item.job_id != job_id:
        raise HTTPException(status_code=404, detail="Item not found")

    db.delete(item)
    log_audit_event(
        db,
        event_type="job_item_deleted",
        entity_type="job",
        entity_id=job_id,
        description=f"Item removed: {item.description}.",
    )
    db.commit()
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/{job_id}/status")
def update_job_status(
    job_id: int,
    status_id: int = Form(...),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    status = db.get(JobStatus, status_id)
    if status is None or not status.is_active:
        raise HTTPException(status_code=400, detail="Selected status was not found")

    old_status_name = job.status.name if job.status else "Received"
    job.status = status
    log_audit_event(
        db,
        event_type="job_status_changed",
        entity_type="job",
        entity_id=job.id,
        description=f"Status changed from {old_status_name} to {status.name}.",
    )
    db.commit()

    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
