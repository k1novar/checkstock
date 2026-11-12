from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Shop(Base):
    __tablename__ = 'shops'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class Product(Base):
    __tablename__ = 'products'
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(120))


class Warehouse(Base):
    __tablename__ = 'warehouses'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class Stock(Base):
    __tablename__ = 'stocks'
    warehouse_id: Mapped[int] = mapped_column(ForeignKey('warehouses.id'), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), primary_key=True)
    physical: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint('physical >= 0'), CheckConstraint('reserved >= 0 AND reserved <= physical'))


class Operation(Base):
    __tablename__ = 'operations'
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'))
    quantity: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[int] = mapped_column(ForeignKey('warehouses.id'))
    target_id: Mapped[int | None] = mapped_column(ForeignKey('warehouses.id'), nullable=True)
    destination: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (CheckConstraint('quantity > 0'), CheckConstraint("kind IN ('fbs','fbo','transfer')"), CheckConstraint("status IN ('reserved','transit','received','cancelled','delivered')"))


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    action: Mapped[str] = mapped_column(String(40))
    product_id: Mapped[int | None] = mapped_column(ForeignKey('products.id'), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(140))
    target: Mapped[str] = mapped_column(String(140))
    operation_id: Mapped[int | None] = mapped_column(ForeignKey('operations.id'), nullable=True)
    # Snapshot of names and exact stock deltas at the time of the operation.
    changes: Mapped[list] = mapped_column(JSON, default=list)


class RequestLog(Base):
    __tablename__ = 'request_log'
    key: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[str] = mapped_column(String(2000))
    response: Mapped[dict] = mapped_column(JSON)
