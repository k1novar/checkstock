import json
from uuid import UUID
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .models import Shop, Product, Warehouse, Stock, Operation, Event, RequestLog


class DomainError(Exception):
    def __init__(self, message, status=409, **details):
        self.message, self.status, self.details = message, status, details


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: UUID
    action: Literal['warehouse_create','warehouse_rename','warehouse_archive','receipt','reserve','transfer','fbo','ship','cancel','receive','deliver']
    product_id: int | None = Field(None, gt=0)
    warehouse_id: int | None = Field(None, gt=0)
    target_id: int | None = Field(None, gt=0)
    operation_id: int | None = Field(None, gt=0)
    quantity: int | None = Field(None, strict=True, gt=0, le=1000000)
    name: str | None = Field(None, min_length=1, max_length=120)
    destination: str | None = Field(None, min_length=1, max_length=120)


def lock_shop(s):
    s.scalar(select(Shop).where(Shop.id == 1).with_for_update())


def get(s, model, id):
    obj = s.get(model, id) if id is not None else None
    if obj is None:
        raise DomainError('Объект не найден или не указан', 404)
    return obj


def stock(s, wid, pid):
    value = s.get(Stock, (wid, pid))
    if value is None:
        value = Stock(warehouse_id=wid, product_id=pid, physical=0, reserved=0)
        s.add(value)
        s.flush()
    return value


def execute(s, cmd):
    lock_shop(s)
    payload = json.dumps(cmd.model_dump(mode='json', exclude={'request_id'}), sort_keys=True, ensure_ascii=False)
    previous = s.get(RequestLog, str(cmd.request_id))
    if previous:
        if previous.payload != payload:
            raise DomainError('Этот ключ запроса уже использован с другими данными')
        return previous.response
    result = apply(s, cmd)
    s.add(RequestLog(key=str(cmd.request_id), payload=payload, response=result))
    s.flush()
    return result


def apply(s, c):
    changes = []
    def change(wid, pid, physical=0, reserved=0):
        item = stock(s, wid, pid)
        item.physical += physical
        item.reserved += reserved
        assert 0 <= item.reserved <= item.physical
        changes.append(dict(warehouse_id=wid, physical=physical, reserved=reserved))

    def log(action, pid=None, qty=0, source='', target='', op=None):
        s.add(Event(action=action, product_id=pid, quantity=qty, source=source, target=target, operation_id=op, changes=changes.copy()))

    if c.action.startswith('warehouse_'):
        if c.action == 'warehouse_create':
            if not c.name:
                raise DomainError('Укажите название склада', 422)
            if s.scalar(select(Warehouse).where(Warehouse.name == c.name)):
                raise DomainError('Склад с таким названием уже существует')
            w = Warehouse(name=c.name)
            s.add(w)
            s.flush()
            log(c.action, target=w.name)
        else:
            w = get(s, Warehouse, c.warehouse_id)
            old = w.name
            if c.action == 'warehouse_archive':
                if not w.archived:
                    w.archived = True
                    log(c.action, source=old, target=f'{old} (архив)')
            else:
                if not c.name:
                    raise DomainError('Укажите название склада', 422)
                other = s.scalar(select(Warehouse).where(Warehouse.name == c.name, Warehouse.id != w.id))
                if other:
                    raise DomainError('Склад с таким названием уже существует')
                w.name = c.name
                log(c.action, source=old, target=w.name)
        return {'warehouse_id': w.id, 'message': 'Склад сохранён'}

    if c.action in ('receipt', 'reserve', 'transfer', 'fbo'):
        p = get(s, Product, c.product_id)
        w = get(s, Warehouse, c.warehouse_id)
        q = c.quantity
        if q is None:
            raise DomainError('Укажите количество', 422)
        if w.archived and c.action != 'transfer':
            raise DomainError('Склад в архиве: доступно только перемещение остатка на действующий ФФ')
        target = None
        if c.action == 'transfer':
            target = get(s, Warehouse, c.target_id)
            if target.id == w.id or target.archived:
                raise DomainError('Выберите другой действующий склад назначения')
        if c.action in ('reserve','fbo') and not c.destination:
            raise DomainError('Укажите заказ FBS или склад маркетплейса FBO', 422)
        item = stock(s, w.id, p.id)
        if c.action != 'receipt' and item.physical - item.reserved < q:
            alternatives = []
            for row in s.scalars(select(Stock).where(Stock.product_id == p.id)):
                wh = s.get(Warehouse, row.warehouse_id)
                alternatives.append({'warehouse': wh.name, 'warehouse_id':wh.id, 'available':row.physical-row.reserved, 'archived':wh.archived})
            raise DomainError('Недостаточно доступного товара на выбранном ФФ. Сначала переместите товар или выберите другой ФФ.', available=item.physical-item.reserved, requested=q, warehouses=alternatives)
        if c.action == 'receipt':
            change(w.id, p.id, physical=q)
            log('receipt', p.id, q, 'Поставщик', w.name)
            return {'message':'Приход проведён'}
        kind = {'reserve':'fbs','transfer':'transfer','fbo':'fbo'}[c.action]
        op = Operation(kind=kind, status='reserved' if kind=='fbs' else 'transit', product_id=p.id, quantity=q, source_id=w.id, target_id=target.id if target else None, destination=target.name if target else c.destination)
        s.add(op)
        s.flush()
        change(w.id, p.id, reserved=q) if kind=='fbs' else change(w.id, p.id, physical=-q)
        log(c.action, p.id, q, w.name, op.destination, op.id)
        return {'operation_id':op.id, 'message':'Резерв создан' if kind=='fbs' else 'Товар отправлен'}

    op = get(s, Operation, c.operation_id)
    source = s.get(Warehouse, op.source_id)
    target = s.get(Warehouse, op.target_id) if op.target_id else None
    transitions = {'ship':('fbs','reserved','transit'), 'cancel':('fbs','reserved','cancelled'), 'deliver':('fbs','transit','delivered')}
    if c.action == 'receive':
        if op.kind not in ('transfer','fbo'):
            raise DomainError('Приёмка применима к перемещениям и FBO')
        expected, final = 'transit', 'received'
    else:
        kind, expected, final = transitions[c.action]
        if op.kind != kind:
            raise DomainError('Действие не подходит для этой операции')
    if op.status == final:
        return {'operation_id':op.id, 'message':'Уже подтверждено; повторного списания нет'}
    if op.status != expected:
        raise DomainError('Недопустимый переход состояния операции')
    if c.action == 'ship':
        change(op.source_id, op.product_id, physical=-op.quantity, reserved=-op.quantity)
    elif c.action == 'cancel':
        change(op.source_id, op.product_id, reserved=-op.quantity)
    elif c.action == 'receive' and op.kind == 'transfer':
        # Existing in-flight shipment can be received after destination is archived.
        change(op.target_id, op.product_id, physical=op.quantity)
    op.status = final
    log(c.action, op.product_id, op.quantity, source.name, target.name if target else op.destination, op.id)
    return {'operation_id':op.id, 'message':'Операция подтверждена'}


def snapshot(s):
    lock_shop(s)
    products = list(s.scalars(select(Product).order_by(Product.id)))
    warehouses = list(s.scalars(select(Warehouse).order_by(Warehouse.id)))
    stocks = {(r.warehouse_id,r.product_id):r for r in s.scalars(select(Stock))}
    operations = list(s.scalars(select(Operation).order_by(Operation.id.desc())))
    events = list(s.scalars(select(Event).order_by(Event.id.desc())))
    rows = []
    for p in products:
        cells = []
        for w in warehouses:
            r = stocks.get((w.id,p.id))
            physical, reserved = (r.physical,r.reserved) if r else (0,0)
            cells.append(dict(warehouse_id=w.id, physical=physical, reserved=reserved, available=physical-reserved))
        ops = [o for o in operations if o.product_id == p.id]
        transit = {kind:sum(o.quantity for o in ops if o.kind == kind and o.status == 'transit') for kind in ('transfer','fbo','fbs')}
        marketplace = sum(o.quantity for o in ops if o.kind=='fbo' and o.status=='received')
        delivered = sum(o.quantity for o in ops if o.kind=='fbs' and o.status=='delivered')
        incoming = sum(e.quantity for e in events if e.product_id == p.id and e.action == 'receipt')
        physical = sum(c['physical'] for c in cells)
        rows.append(dict(id=p.id, sku=p.sku, name=p.name, stocks=cells, physical=physical, reserved=sum(c['reserved'] for c in cells), available=sum(c['available'] for c in cells), transit=transit, marketplace=marketplace, delivered=delivered, incoming=incoming, balanced=incoming == physical + sum(transit.values()) + marketplace + delivered))
    return dict(shop='Демо-магазин', warehouses=[dict(id=w.id,name=w.name,archived=w.archived) for w in warehouses], products=rows, operations=[dict(id=o.id,kind=o.kind,status=o.status,product_id=o.product_id,quantity=o.quantity,source_id=o.source_id,target_id=o.target_id,destination=o.destination,created_at=o.created_at.isoformat()) for o in operations], events=[dict(id=e.id,at=e.at.isoformat(),action=e.action,product_id=e.product_id,quantity=e.quantity,source=e.source,target=e.target,operation_id=e.operation_id,changes=e.changes) for e in events])
