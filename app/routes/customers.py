from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditLog, Customer, Job
from app.services.audit_service import log_audit_event
from app.template_context import templates

router = APIRouter(prefix="/customers", tags=["customers"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def list_customers(
    request: Request,
    q: str = Query(""),
    db: Session = Depends(get_db),
):
    query = db.query(Customer)
    search = q.strip()
    if search:
        query = query.filter(
            Customer.name.ilike(f"%{search}%")
            | Customer.phone.ilike(f"%{search}%")
            | Customer.email.ilike(f"%{search}%")
            | Customer.company_name.ilike(f"%{search}%")
        )
    customers = query.order_by(Customer.name.asc()).all()
    return templates.TemplateResponse(
        "customers/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "customers",
            "q": q,
            "customers": customers,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_customer(request: Request):
    return templates.TemplateResponse(
        "customers/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "customers",
            "customer": None,
            "form_action": "/customers",
            "page_title": "New customer",
        },
    )


@router.post("")
def create_customer(
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    company_name: str = Form(""),
    business_id: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Customer name is required")

    customer = Customer(
        name=name.strip(),
        phone=phone.strip() or None,
        email=email.strip() or None,
        address=address.strip() or None,
        company_name=company_name.strip() or None,
        business_id=business_id.strip() or None,
        notes=notes.strip() or None,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    log_audit_event(
        db,
        event_type="customer_created",
        entity_type="customer",
        entity_id=customer.id,
        description="Customer created.",
    )
    db.commit()

    return RedirectResponse(url=f"/customers/{customer.id}", status_code=303)


@router.get("/{customer_id}", response_class=HTMLResponse)
def customer_detail(customer_id: int, request: Request, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    customer_jobs = (
        db.query(Job)
        .filter(Job.customer_id == customer.id)
        .order_by(Job.created_at.desc())
        .all()
    )
    audit_events = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "customer", AuditLog.entity_id == customer.id)
        .order_by(AuditLog.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        "customers/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "customers",
            "customer": customer,
            "customer_jobs": customer_jobs,
            "audit_events": audit_events,
        },
    )


@router.get("/{customer_id}/edit", response_class=HTMLResponse)
def edit_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    return templates.TemplateResponse(
        "customers/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "customers",
            "customer": customer,
            "form_action": f"/customers/{customer.id}",
            "page_title": "Edit customer",
        },
    )


@router.post("/{customer_id}")
def update_customer(
    customer_id: int,
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    company_name: str = Form(""),
    business_id: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Customer name is required")

    customer.name = name.strip()
    customer.phone = phone.strip() or None
    customer.email = email.strip() or None
    customer.address = address.strip() or None
    customer.company_name = company_name.strip() or None
    customer.business_id = business_id.strip() or None
    customer.notes = notes.strip() or None

    db.commit()
    return RedirectResponse(url=f"/customers/{customer.id}", status_code=303)


@router.post("/{customer_id}/delete")
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if customer.jobs:
        raise HTTPException(
            status_code=400,
            detail="Customer has job history and cannot be deleted.",
        )

    db.delete(customer)
    db.commit()
    return RedirectResponse(url="/customers", status_code=303)
