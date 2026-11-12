import os
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from .models import Base, Shop, Product, Warehouse, Stock, Event


def make_engine(url=None):
    url = url or os.getenv('DATABASE_URL', 'postgresql+psycopg://checkstock:checkstock@localhost:5432/checkstock')
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def configure_sqlite(dbapi_connection, _):
            dbapi_connection.execute('PRAGMA foreign_keys=ON')
    return engine


def initialize(engine, seed=True):
    # Idempotent schema bootstrap for the prototype. Run once before API workers.
    Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        if s.get(Shop, 1) is None:
            s.add(Shop(id=1, name='Демо-магазин'))
            s.flush()
        s.scalar(select(Shop).where(Shop.id == 1).with_for_update())
        if seed and s.scalar(select(Product.id).limit(1)) is None:
            s.add_all([Product(id=1, sku='SKU-101', name='Органайзер'), Product(id=2, sku='SKU-102', name='Контейнер'), Product(id=3, sku='SKU-103', name='Корзина')])
            s.add_all([Warehouse(id=1, name='Север'), Warehouse(id=2, name='Юг')])
            s.flush()
            # Let PostgreSQL's generated sequence allocate future warehouse IDs.
            if engine.dialect.name == 'postgresql':
                from sqlalchemy import text
                s.execute(text("SELECT setval(pg_get_serial_sequence('warehouses','id'), 2)"))
            for wid, pid, qty in [(1,1,10),(1,2,5),(1,3,0),(2,1,6),(2,2,0),(2,3,4)]:
                s.add(Stock(warehouse_id=wid, product_id=pid, physical=qty, reserved=0))
                if qty:
                    s.add(Event(action='receipt', product_id=pid, quantity=qty, source='Поставщик', target={1:'Север',2:'Юг'}[wid], changes=[{'warehouse_id':wid,'physical':qty,'reserved':0}]))


if __name__ == '__main__':
    initialize(make_engine())
