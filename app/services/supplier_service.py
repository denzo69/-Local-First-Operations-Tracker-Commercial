from sqlalchemy.orm import Session

from app.models import Supplier


def resolve_goods_receipt_supplier(db: Session, *, supplier_id: str | int | None, supplier_name: str | None) -> Supplier:
    selected_supplier_id = str(supplier_id or "").strip()
    manual_name = (supplier_name or "").strip()

    if selected_supplier_id:
        supplier = db.get(Supplier, int(selected_supplier_id))
        if supplier is None or not supplier.is_active:
            raise ValueError("Active supplier is required.")
        return supplier

    if not manual_name:
        raise ValueError("Supplier name is required.")

    supplier = (
        db.query(Supplier)
        .filter(Supplier.name == manual_name)
        .first()
    )
    if supplier is None:
        supplier = Supplier(name=manual_name, is_active=True)
        db.add(supplier)
        db.flush()
    elif not supplier.is_active:
        supplier.is_active = True
        db.flush()
    return supplier
