from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes import jobs

router = APIRouter(prefix="/work-orders", tags=["work-orders"])


@router.get("", response_class=HTMLResponse)
def list_work_orders(
    request: Request,
    view: str = Query("active"),
    q: str = Query(""),
    db: Session = Depends(get_db),
):
    return jobs.list_jobs(request=request, view=view, q=q, db=db)


@router.get("/new", response_class=HTMLResponse)
def new_work_order(request: Request, db: Session = Depends(get_db)):
    return jobs.new_job(request=request, db=db)


@router.post("")
def create_work_order(
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
    return jobs.create_job(
        request=request,
        title=title,
        customer_id=customer_id,
        description=description,
        arrival_date=arrival_date,
        requested_pickup_date=requested_pickup_date,
        priority=priority,
        status_id=status_id,
        notes=notes,
        db=db,
    )


@router.get("/{job_id}/edit", response_class=HTMLResponse)
def edit_work_order(job_id: int, request: Request, db: Session = Depends(get_db)):
    return jobs.edit_job(job_id=job_id, request=request, db=db)


@router.post("/{job_id}")
def update_work_order(
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
    return jobs.update_job(
        request=request,
        job_id=job_id,
        title=title,
        customer_id=customer_id,
        description=description,
        arrival_date=arrival_date,
        requested_pickup_date=requested_pickup_date,
        priority=priority,
        status_id=status_id,
        notes=notes,
        db=db,
    )


@router.get("/{job_id}", response_class=HTMLResponse)
def work_order_detail(job_id: int, request: Request, db: Session = Depends(get_db)):
    return jobs.job_detail(job_id=job_id, request=request, db=db)


@router.get("/{job_id}/receipt", response_class=HTMLResponse)
def work_order_receipt(job_id: int, request: Request, db: Session = Depends(get_db)):
    return jobs.job_receipt(job_id=job_id, request=request, db=db)


@router.get("/{job_id}/print/work-order", response_class=HTMLResponse)
def print_work_order(job_id: int, request: Request, db: Session = Depends(get_db)):
    return jobs.job_receipt(job_id=job_id, request=request, db=db)


@router.get("/{job_id}/print/receipt", response_class=HTMLResponse)
def print_receipt(job_id: int, request: Request, db: Session = Depends(get_db)):
    return jobs.job_receipt(job_id=job_id, request=request, db=db)


@router.post("/{job_id}/items")
def add_work_order_item(
    request: Request,
    job_id: int,
    product_id: str = Form(""),
    description: str = Form(""),
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    vat_percent: str = Form("24"),
    db: Session = Depends(get_db),
):
    return jobs.add_job_item(
        request=request,
        job_id=job_id,
        product_id=product_id,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        vat_percent=vat_percent,
        db=db,
    )


@router.post("/{job_id}/items/{item_id}/delete")
def delete_work_order_item(
    request: Request,
    job_id: int,
    item_id: int,
    db: Session = Depends(get_db),
):
    return jobs.delete_job_item(request=request, job_id=job_id, item_id=item_id, db=db)


@router.post("/{job_id}/status")
def update_work_order_status(
    request: Request,
    job_id: int,
    status_id: int = Form(...),
    db: Session = Depends(get_db),
):
    return jobs.update_job_status(request=request, job_id=job_id, status_id=status_id, db=db)


@router.post("/{job_id}/delete")
def delete_work_order(request: Request, job_id: int, db: Session = Depends(get_db)):
    return jobs.delete_job(request=request, job_id=job_id, db=db)
