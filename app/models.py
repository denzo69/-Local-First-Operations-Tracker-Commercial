from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    phone = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    company_name = Column(String(255), nullable=True)
    business_id = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    jobs = relationship("Job", back_populates="customer")


class JobStatus(Base):
    __tablename__ = "job_statuses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    sort_order = Column(Integer, default=0)
    is_final = Column(Boolean, default=False)
    is_ready_state = Column(Boolean, default=False)
    is_packed_state = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    jobs = relationship("Job", back_populates="status")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_number = Column(String(100), nullable=True, index=True)
    receipt_number = Column(String(100), nullable=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    arrival_date = Column(Date, nullable=True)
    requested_pickup_date = Column(Date, nullable=True, index=True)
    status_id = Column(Integer, ForeignKey("job_statuses.id"), nullable=True)
    priority = Column(String(50), default="normal")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="jobs")
    status = relationship("JobStatus", back_populates="jobs")
    items = relationship("JobItem", back_populates="job")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    unit_price = Column(Float, default=0.0)
    vat_percent = Column(Float, default=24.0)
    unit = Column(String(50), default="pcs")
    is_active = Column(Boolean, default=True)
    is_stock_item = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    job_items = relationship("JobItem", back_populates="product")


class JobItem(Base):
    __tablename__ = "job_items"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    description = Column(String(255), nullable=False)
    quantity = Column(Float, default=1.0)
    unit_price = Column(Float, default=0.0)
    vat_percent = Column(Float, default=24.0)
    line_total = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    job = relationship("Job", back_populates="items")
    product = relationship("Product", back_populates="job_items")


class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    receipt_number = Column(String(100), nullable=False, index=True)
    receipt_type = Column(String(100), default="incoming")
    printed_at = Column(DateTime, nullable=True)
    editable_snapshot = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(100), nullable=False)
    entity_type = Column(String(100), nullable=True)
    entity_id = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
