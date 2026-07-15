from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes import jobs

router = APIRouter(prefix="/delivery-notes", tags=["delivery-notes"])


legacy_router = APIRouter(tags=["delivery-notes"])


@legacy_router.get("/delivery_notes")
@legacy_router.get("/delivery-note")
@legacy_router.get("/Delivery Notes")
def redirect_legacy_delivery_notes():
    return RedirectResponse(url="/delivery-notes", status_code=303)


@router.get("", response_class=HTMLResponse)
def list_delivery_notes(request: Request, view: str = Query("active"), q: str = Query(""), db: Session = Depends(get_db)):
    return jobs.list_jobs(request=request, view=view, q=q, db=db)


@router.get("/new", response_class=HTMLResponse)
def new_delivery_note(request: Request, db: Session = Depends(get_db)):
    return jobs.new_job(request=request, db=db)


@router.post("")
def create_delivery_note(
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
    return jobs.create_job(request, title, customer_id, description, arrival_date, requested_pickup_date, priority, status_id, notes, db)


@router.get("/{job_id}", response_class=HTMLResponse)
def delivery_note_detail(job_id: int, request: Request, db: Session = Depends(get_db)):
    return jobs.job_detail(job_id=job_id, request=request, db=db)


@router.get("/{job_id}/edit", response_class=HTMLResponse)
def edit_delivery_note(job_id: int, request: Request, db: Session = Depends(get_db)):
    return jobs.edit_job(job_id=job_id, request=request, db=db)


@router.post("/{job_id}")
def update_delivery_note(
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
    return jobs.update_job(request, job_id, title, customer_id, description, arrival_date, requested_pickup_date, priority, status_id, notes, db)


@router.post("/{job_id}/items")
def add_delivery_note_item(
    request: Request,
    job_id: int,
    product_id: str = Form(""),
    description: str = Form(""),
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    vat_percent: str = Form("24"),
    db: Session = Depends(get_db),
):
    return jobs.add_job_item(request, job_id, product_id, description, quantity, unit_price, vat_percent, db)


@router.post("/{job_id}/items/{item_id}/delete")
def delete_delivery_note_item(request: Request, job_id: int, item_id: int, db: Session = Depends(get_db)):
    return jobs.delete_job_item(request=request, job_id=job_id, item_id=item_id, db=db)


@router.post("/{job_id}/status")
def update_delivery_note_status(request: Request, job_id: int, status_id: int = Form(...), db: Session = Depends(get_db)):
    return jobs.update_job_status(request=request, job_id=job_id, status_id=status_id, db=db)


@router.post("/{job_id}/convert/{target_type}")
def convert_delivery_note(request: Request, job_id: int, target_type: str, payment_method: str = Form("cash"), db: Session = Depends(get_db)):
    return jobs.convert_job_document(request=request, job_id=job_id, target_type=target_type, payment_method=payment_method, db=db)
