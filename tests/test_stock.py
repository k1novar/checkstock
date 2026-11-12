from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import random
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import create_app
from app.db import initialize
from app.models import Stock, Event


def post(client, action, expected=200, **fields):
    res = client.post('/api/commands', json={'request_id':str(uuid4()),'action':action,**fields})
    assert res.status_code == expected, res.text
    return res.json()


def state(client):
    res = client.get('/api/state')
    assert res.status_code == 200
    result = res.json()
    assert all(p['balanced'] for p in result['products'])
    for p in result['products']:
        for c in p['stocks']:
            assert 0 <= c['reserved'] <= c['physical']
            assert c['available'] == c['physical'] - c['reserved']
    return result


def product(client, id=1):
    return next(p for p in state(client)['products'] if p['id']==id)


def create(client, action, quantity=3, **extra):
    fields = {'product_id':1,'warehouse_id':1,'quantity':quantity}
    if action == 'transfer': fields['target_id'] = 2
    if action in ('reserve','fbo'): fields['destination'] = 'Заказ 1' if action=='reserve' else 'WB Коледино'
    return post(client, action, **(fields|extra))


def test_seed_and_second_boot(client, engine):
    assert [(p['physical'],p['available']) for p in state(client)['products']] == [(16,16),(5,5),(4,4)]
    initialize(engine)
    assert product(client)['physical'] == 16
    assert len(state(client)['events']) == 4
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    assert client.get('/health').json()=={'status':'ok'}


def test_receipt_and_exact_replay(client):
    key=str(uuid4())
    create(client,'receipt',4,request_id=key)
    create(client,'receipt',4,request_id=key)
    assert product(client)['physical']==20
    post(client,'receipt',expected=409,request_id=key,product_id=1,warehouse_id=1,quantity=5)
    assert product(client)['physical']==20


def test_fbs_lifecycle(client):
    op=create(client,'reserve',3)['operation_id']
    p=product(client)
    assert (p['physical'],p['reserved'],p['available'],p['transit']['fbs'])==(16,3,13,0)
    post(client,'ship',operation_id=op)
    post(client,'ship',operation_id=op)
    p=product(client)
    assert (p['physical'],p['reserved'],p['available'],p['transit']['fbs'])==(13,0,13,3)
    post(client,'cancel',expected=409,operation_id=op)
    post(client,'deliver',operation_id=op)
    post(client,'deliver',operation_id=op)
    p=product(client)
    assert p['transit']['fbs']==0 and p['delivered']==3


def test_cancel_releases_only_reservation(client):
    op=create(client,'reserve',10)['operation_id']
    post(client,'cancel',operation_id=op)
    post(client,'cancel',operation_id=op)
    post(client,'ship',expected=409,operation_id=op)
    p=product(client)
    assert (p['physical'],p['reserved'],p['available'])==(16,0,16)


@pytest.mark.parametrize('action',['transfer','fbo'])
def test_transfer_and_fbo_lifecycle(client,action):
    op=create(client,action,4)['operation_id']
    p=product(client)
    assert p['physical']==12 and p['transit'][action]==4
    post(client,'receive',operation_id=op)
    post(client,'receive',operation_id=op)
    p=product(client)
    assert p['transit'][action]==0
    if action=='fbo': assert p['marketplace']==4 and p['physical']==12
    else: assert p['physical']==16 and p['stocks'][1]['physical']==10


@pytest.mark.parametrize('action',['reserve','transfer','fbo'])
def test_local_shortage_despite_total_stock(client,action):
    result=create(client,action,12,expected=409)
    assert result['available']==10 and result['requested']==12
    assert sum(w['available'] for w in result['warehouses'])==16
    p=product(client)
    assert p['physical']==16 and p['reserved']==0
    assert len(state(client)['events'])==4


def test_reserved_stock_cannot_be_shipped_elsewhere(client):
    create(client,'reserve',9)
    create(client,'transfer',2,expected=409)
    create(client,'fbo',2,expected=409)
    assert product(client)['stocks'][0]['available']==1


def test_archive_with_stock_and_pending_operations(client):
    reserved=create(client,'reserve',2)['operation_id']
    incoming=create(client,'transfer',3,warehouse_id=2,target_id=1)['operation_id']
    post(client,'warehouse_archive',warehouse_id=1)
    s=state(client)
    assert s['warehouses'][0]['archived']
    assert product(client)['physical']==13
    for a in ('receipt','reserve','fbo'):
        create(client,a,1,expected=409)
    create(client,'transfer',1,warehouse_id=2,target_id=1,expected=409)
    post(client,'ship',operation_id=reserved)
    post(client,'receive',operation_id=incoming)
    outgoing=create(client,'transfer',11)['operation_id']
    post(client,'receive',operation_id=outgoing)
    p=product(client)
    assert p['stocks'][0]['physical']==0 and p['stocks'][1]['physical']==14
    assert p['transit']['fbs']==2


def test_archive_cancel_and_repeat(client):
    op=create(client,'reserve',2)['operation_id']
    post(client,'warehouse_archive',warehouse_id=1)
    post(client,'warehouse_archive',warehouse_id=1)
    post(client,'cancel',operation_id=op)
    assert product(client)['available']==16
    assert len([e for e in state(client)['events'] if e['action']=='warehouse_archive'])==1


def test_create_rename_history_and_zero_stock(client):
    wid=post(client,'warehouse_create',name='Восток')['warehouse_id']
    assert wid>2
    assert all(p['stocks'][-1]['physical']==0 for p in state(client)['products'])
    create(client,'receipt',2,warehouse_id=wid)
    post(client,'warehouse_rename',warehouse_id=wid,name='Восток новый')
    s=state(client)
    assert s['warehouses'][-1]['name']=='Восток новый'
    assert any(e['action']=='receipt' and e['target']=='Восток' for e in s['events'])
    post(client,'warehouse_create',name='Север',expected=409)
    post(client,'warehouse_rename',warehouse_id=wid,name='Юг',expected=409)


@pytest.mark.parametrize('qty',[0,-1,1.5,'2',True,1000001,None])
def test_invalid_quantity(client,qty):
    create(client,'receipt',qty,expected=422)
    assert product(client)['physical']==16


@pytest.mark.parametrize('action',['ship','deliver','cancel'])
def test_invalid_operation_kind(client,action):
    op=create(client,'fbo')['operation_id']
    post(client,action,operation_id=op,expected=409)
    assert product(client)['transit']['fbo']==3


def test_invalid_transitions_and_ids(client):
    op=create(client,'reserve')['operation_id']
    post(client,'receive',operation_id=op,expected=409)
    post(client,'deliver',operation_id=op,expected=409)
    post(client,'ship',operation_id=999,expected=404)
    create(client,'transfer',1,target_id=1,expected=409)
    create(client,'receipt',1,product_id=999,expected=404)
    create(client,'receipt',1,warehouse_id=999,expected=404)
    create(client,'reserve',1,destination='  ',expected=422)
    post(client,'warehouse_create',name='  ',expected=422)
    assert product(client)['reserved']==3


def test_full_demo_scenario(client):
    reserve=create(client,'reserve',3)['operation_id']
    create(client,'transfer',8,expected=409)
    transfer=create(client,'transfer',4)['operation_id']
    post(client,'receive',operation_id=transfer)
    post(client,'receive',operation_id=transfer)
    fbo=create(client,'fbo',2,warehouse_id=2)['operation_id']
    post(client,'ship',operation_id=reserve)
    post(client,'ship',operation_id=reserve)
    post(client,'receive',operation_id=fbo)
    post(client,'warehouse_archive',warehouse_id=1)
    p=product(client)
    assert [s['physical'] for s in p['stocks']]==[3,8]
    assert p['marketplace']==2 and p['transit']['fbs']==3
    assert p['incoming']==16


def test_journal_reconstructs_stock_after_randomized_operations(client,engine):
    rng=random.Random(42)
    for _ in range(70):
        s=state(client)
        active=[o for o in s['operations'] if o['status'] in ('reserved','transit')]
        if active and rng.random()<.5:
            op=rng.choice(active)
            action=rng.choice(['ship','cancel']) if op['status']=='reserved' else ('deliver' if op['kind']=='fbs' else 'receive')
            post(client,action,operation_id=op['id'])
        else:
            p=rng.choice(s['products']);wid=rng.choice([1,2]);available=p['stocks'][wid-1]['available']
            action=rng.choice(['receipt','reserve','transfer','fbo']) if available else 'receipt'
            q=rng.randint(1, min(available,5)) if action!='receipt' else rng.randint(1,5)
            create(client,action,q,product_id=p['id'],warehouse_id=wid,**({'target_id':3-wid} if action=='transfer' else {}))
    state(client)
    with Session(engine) as s:
        totals={}
        for e in s.scalars(select(Event)):
            for delta in e.changes:
                key=(delta['warehouse_id'],e.product_id)
                totals.setdefault(key,[0,0])
                totals[key][0]+=delta['physical'];totals[key][1]+=delta['reserved']
        for r in s.scalars(select(Stock)):
            assert [r.physical,r.reserved]==totals.get((r.warehouse_id,r.product_id),[0,0])


def parallel(engine, payloads):
    def run(payload):
        with TestClient(create_app(engine)) as c:
            return c.post('/api/commands',json=payload)
    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return list(pool.map(run,payloads))


def require_postgres(engine):
    if engine.dialect.name!='postgresql':pytest.skip('Real PostgreSQL required for row-lock concurrency test')


def test_concurrent_reservation_prevents_oversell(client,engine):
    require_postgres(engine)
    results=parallel(engine,[dict(request_id=str(uuid4()),action='reserve',product_id=1,warehouse_id=1,quantity=7,destination='Order') for _ in range(2)])
    assert sorted(r.status_code for r in results)==[200,409]
    assert product(client)['reserved']==7


def test_concurrent_same_request(client,engine):
    require_postgres(engine)
    payload=dict(request_id=str(uuid4()),action='receipt',product_id=1,warehouse_id=1,quantity=5)
    results=parallel(engine,[payload]*4)
    assert all(r.status_code==200 for r in results)
    assert product(client)['physical']==21
    assert len(state(client)['events'])==5


def test_concurrent_confirmations(client,engine):
    require_postgres(engine)
    op=create(client,'transfer',4)['operation_id']
    results=parallel(engine,[dict(request_id=str(uuid4()),action='receive',operation_id=op) for _ in range(4)])
    assert all(r.status_code==200 for r in results)
    assert product(client)['stocks'][1]['physical']==10
    assert len([e for e in state(client)['events'] if e['action']=='receive'])==1


def test_concurrent_cancel_vs_ship(client,engine):
    require_postgres(engine)
    op=create(client,'reserve',4)['operation_id']
    results=parallel(engine,[dict(request_id=str(uuid4()),action=a,operation_id=op) for a in ('ship','cancel')])
    assert sorted(r.status_code for r in results)==[200,409]
    p=product(client)
    assert p['reserved']==0
    assert (p['physical'],p['transit']['fbs']) in [(12,4),(16,0)]
