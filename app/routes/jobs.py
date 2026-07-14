from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditLog, Customer, Job, JobItem, JobStatus, Product, utc_now
from app.services.auth_service import request_current_user
from app.services.audit_service import log_audit_event
from app.services.money_service import line_total as calculate_line_total
from app.services.money_service import parse_decimal
from app.services.money_service import sum_money
from app.services.print_service import build_print_context
from app.services.receipt_number_service import allocate_receipt_number
from app.services.sales_service import PaymentInput, create_sale_from_work_order
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
        "name": "Waiting",
        "sort_order": 30,
        "is_final": False,
        "is_ready_state": False,
        "is_packed_state": False,
    },
    {
        "name": "Ready",
        "sort_order": 40,
        "is_final": False,
        "is_ready_state": True,
        "is_packed_state": False,
    },
    {
        "name": "Completed",
        "sort_order": 50,
        "is_final": True,
        "is_ready_state": False,
        "is_packed_state": False,
    },
]


def ensure_default_job_statuses(db: Session) -> list[JobStatus]:
    if db.query(JobStatus).count() == 0:
        for status_data in DEFAULT_JOB_STATUSES:
            db.add(JobStatus(**status_data))
        db.commit()

    return (
        db.query(JobStatus)
        .filter(JobStatus.is_active.is_(True))
        .order_by(JobStatus.sort_order.asc(), JobStatus.name.asc())
        .all()
    )


def get_received_status(db: Session) -> JobStatus:
    statuses = ensure_default_job_statuses(db)
    received = next((status for status in statuses if status.name == "Received"), None)
    return received or statuses[0]


def ensure_receipt_number(db: Session, job: Job) -> str:
    if job.receipt_number:
        return job.receipt_number

    receipt_date = job.created_at.date() if job.created_at else date.today()
    job.receipt_number = allocate_receipt_number(db, receipt_date)
    db.commit()
    db.refresh(job)
    return job.receipt_number


def parse_optional_date(value: str) -> date | None:
    if not value.strip():
        return None
    return date.fromisoformat(value)


DOCUMENT_TYPES = {"work_order", "delivery_note", "quote"}


def document_type_for(request: Request) -> str:
    path = request.url.path
    if path.startswith("/delivery-notes"):
        return "delivery_note"
    if path.startswith("/quotes"):
        return "quote"
    return "work_order"


def route_base_for(request: Request) -> str:
    path = request.url.path
    if path.startswith("/delivery-notes"):
        return "/delivery-notes"
    if path.startswith("/quotes"):
        return "/quotes"
    return "/work-orders" if path.startswith("/work-orders") else "/jobs"


def active_page_for(document_type: str) -> str:
    return {
        "work_order": "jobs",
        "delivery_note": "delivery_notes",
        "quote": "quotes",
    }.get(document_type, "jobs")


def document_label_key(document_type: str) -> str:
    return {
        "work_order": "work_order",
        "delivery_note": "delivery_note",
        "quote": "quote",
    }.get(document_type, "work_order")


def optional_form_int(value: str | int | None, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a valid number") from exc


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
    document_type = document_type_for(request)
    query = db.query(Job).join(Job.status, isouter=True)
    query = query.filter(Job.document_type == document_type)

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
    route_base = route_base_for(request)
    return templates.TemplateResponse(
        "jobs/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": active_page_for(document_type),
            "document_type": document_type,
            "document_label_key": document_label_key(document_type),
            "view": view,
            "q": q,
            "jobs": jobs,
            "route_base": route_base,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_job(request: Request, db: Session = Depends(get_db)):
    customers = db.query(Customer).order_by(Customer.name.asc()).all()
    statuses = ensure_default_job_statuses(db)
    route_base = route_base_for(request)
    document_type = document_type_for(request)
    return templates.TemplateResponse(
        "jobs/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": active_page_for(document_type),
            "customers": customers,
            "statuses": statuses,
            "job": None,
            "form_action": route_base,
            "document_type": document_type,
            "document_label_key": document_label_key(document_type),
            "page_title": "New document",
            "route_base": route_base,
        },
    )


@router.post("")
def create_job(
    request: Request,
    title: str = Form(...),
    customer_id: str = Form(""),
    description: str = Form(""),
    arrival_date: str = Form(""),
    requested_pickup_date: str = Form(""),
    priority: str = Form("normal"),
    status_id: int | None = Form(None),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    if not title.strip():
        raise HTTPException(status_code=400, detail="Work order title is required")

    parsed_customer_id = optional_form_int(customer_id, "Customer")
    customer = db.get(Customer, parsed_customer_id) if parsed_customer_id else None
    if parsed_customer_id and customer is None:
        raise HTTPException(status_code=400, detail="Selected customer was not found")

    try:
        parsed_arrival_date = parse_optional_date(arrival_date)
        parsed_pickup_date = parse_optional_date(requested_pickup_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format") from exc

    status = db.get(JobStatus, status_id) if status_id else get_received_status(db)
    if status_id and status is None:
        raise HTTPException(status_code=400, detail="Selected status was not found")

    document_type = document_type_for(request)
    job = Job(
        title=title.strip(),
        document_type=document_type,
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
        event_type=f"{document_type}.created",
        entity_type="job",
        entity_id=job.id,
        description=f"{document_type} created with status {job.status.name if job.status else 'Received'}.",
    )
    db.commit()

    return RedirectResponse(url=f"{route_base_for(request)}/{job.id}", status_code=303)


@router.get("/{job_id}/edit", response_class=HTMLResponse)
def edit_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Work order not found")

    customers = db.query(Customer).order_by(Customer.name.asc()).all()
    statuses = ensure_default_job_statuses(db)
    route_base = route_base_for(request)
    document_type = job.document_type or document_type_for(request)
    return templates.TemplateResponse(
        "jobs/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": active_page_for(document_type),
            "customers": customers,
            "statuses": statuses,
            "job": job,
            "form_action": f"{route_base}/{job.id}",
            "document_type": document_type,
            "document_label_key": document_label_key(document_type),
            "page_title": "Edit document",
            "route_base": route_base,
        },
    )


@router.post("/{job_id}")
def update_job(
    request: Request,
    job_id: int,
    title: str = Form(...),
    customer_id: str = Form(""),
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
        raise HTTPException(status_code=404, detail="Work order not found")
    if not title.strip():
        raise HTTPException(status_code=400, detail="Work order title is required")

    parsed_customer_id = optional_form_int(customer_id, "Customer")
    customer = db.get(Customer, parsed_customer_id) if parsed_customer_id else None
    if parsed_customer_id and customer is None:
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
        description="Work order details updated.",
    )
    db.commit()
    return RedirectResponse(url=f"{route_base_for(request)}/{job.id}", status_code=303)


@router.get("/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Work order not found")
    statuses = ensure_default_job_statuses(db)
    document_type = job.document_type or document_type_for(request)
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
            "active_page": active_page_for(document_type),
            "job": job,
            "document_type": document_type,
            "document_label_key": document_label_key(document_type),
            "statuses": statuses,
            "products": products,
            "audit_events": audit_events,
            "job_total": sum_money(item.line_total for item in job.items),
            "route_base": route_base_for(request),
        },
    )


@router.get("/{job_id}/receipt", response_class=HTMLResponse)
def job_receipt(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Work order not found")
    ensure_receipt_number(db, job)
    print_context = build_print_context(db, job, "customer_receipt")

    return templates.TemplateResponse(
        "jobs/receipt.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": active_page_for(job.document_type or document_type_for(request)),
            "job": job,
            **print_context,
            "route_base": route_base_for(request),
        },
    )


@router.post("/{job_id}/items")
def add_job_item(
    request: Request,
    job_id: int,
    product_id: str = Form(""),
    description: str = Form(""),
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    vat_percent: str = Form("24"),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Work order not found")

    parsed_product_id = optional_form_int(product_id, "Product")
    product = db.get(Product, parsed_product_id) if parsed_product_id else None
    if parsed_product_id and product is None:
        raise HTTPException(status_code=400, detail="Selected product was not found")
    parsed_quantity = parse_decimal(quantity, "1")
    parsed_unit_price = parse_decimal(unit_price, "0")
    parsed_vat_percent = parse_decimal(vat_percent, "24")
    item_description = description.strip()

    if product is not None and not item_description:
        item_description = product.name
        parsed_unit_price = parse_decimal(product.unit_price)
        parsed_vat_percent = parse_decimal(product.vat_percent)

    if not item_description:
        raise HTTPException(status_code=400, detail="Item description is required")

    line_total = calculate_line_total(parsed_quantity, parsed_unit_price)
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
    return RedirectResponse(url=f"{route_base_for(request)}/{job.id}", status_code=303)


@router.post("/{job_id}/items/{item_id}/delete")
def delete_job_item(
    request: Request,
    job_id: int,
    item_id: int,
    db: Session = Depends(get_db),
):
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
    return RedirectResponse(url=f"{route_base_for(request)}/{job_id}", status_code=303)


@router.post("/{job_id}/status")
def update_job_status(
    request: Request,
    job_id: int,
    status_id: int = Form(...),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Work order not found")

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

    return RedirectResponse(url=f"{route_base_for(request)}/{job.id}", status_code=303)


@router.post("/{job_id}/delete")
def delete_job(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Work order not found")

    for item in list(job.items):
        db.delete(item)
    log_audit_event(
        db,
        event_type="job_deleted",
        entity_type="job",
        entity_id=job.id,
        description="Work order deleted.",
    )
    db.delete(job)
    db.commit()
    return RedirectResponse(url=route_base_for(request), status_code=303)


def clone_job_as_type(db: Session, *, source_job: Job, target_type: str) -> Job:
    if target_type not in DOCUMENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid target document type")
    existing = (
        db.query(Job)
        .filter(Job.source_job_id == source_job.id, Job.document_type == target_type)
        .order_by(Job.id.asc())
        .first()
    )
    if existing is not None:
        return existing
    cloned = Job(
        document_type=target_type,
        source_job_id=source_job.id,
        title=source_job.title,
        customer=source_job.customer,
        description=source_job.description,
        arrival_date=date.today(),
        requested_pickup_date=source_job.requested_pickup_date,
        status=get_received_status(db),
        priority=source_job.priority,
        notes=source_job.notes,
    )
    db.add(cloned)
    db.flush()
    for item in source_job.items:
        db.add(
            JobItem(
                job=cloned,
                product=item.product,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                vat_percent=item.vat_percent,
                line_total=item.line_total,
            )
        )
    source_job.converted_at = utc_now()
    db.flush()
    ensure_receipt_number(db, cloned)
    log_audit_event(
        db,
        event_type=f"{source_job.document_type}.converted",
        entity_type="job",
        entity_id=source_job.id,
        description=f"{source_job.document_type} converted to {target_type} #{cloned.id}.",
    )
    db.commit()
    db.refresh(cloned)
    return cloned


@router.post("/{job_id}/convert/{target_type}")
def convert_job_document(
    request: Request,
    job_id: int,
    target_type: str,
    payment_method: str = Form("cash"),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if target_type in {"work_order", "delivery_note", "quote"}:
        converted = clone_job_as_type(db, source_job=job, target_type=target_type)
        return RedirectResponse(url=f"/{target_type.replace('_', '-') + 's' if target_type != 'work_order' else 'work-orders'}/{converted.id}", status_code=303)
    if target_type in {"sale", "invoice"}:
        current_user = request_current_user(request)
        try:
            sale = create_sale_from_work_order(
                db,
                work_order_id=job.id,
                shift_id=None,
                seller_id=None,
                seller_mode="default",
                payments=[PaymentInput("invoice" if target_type == "invoice" else payment_method)],
                send_to_invoice=target_type == "invoice",
                created_by_user_id=current_user.id if current_user is not None else None,
                idempotency_key=f"{job.document_type}-{job.id}-{target_type}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=f"/sales/{sale.id}", status_code=303)
    raise HTTPException(status_code=400, detail="Invalid conversion target")
