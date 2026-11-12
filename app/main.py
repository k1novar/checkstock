from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from .db import make_engine
from .service import Command, DomainError, execute, snapshot


def create_app(engine=None):
    engine = engine if engine is not None else make_engine()
    app = FastAPI(title='CheckStock', version='1.0.0', description='Один магазин. Количества — целые единицы. POST /api/commands требует UUID request_id.')
    app.state.engine = engine
    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={'detail':exc.message, **exc.details})

    @app.exception_handler(OperationalError)
    async def database_error(request: Request, exc: OperationalError):
        return JSONResponse(status_code=503, content={'detail':'База временно недоступна. Повторите запрос с тем же request_id.'})

    @app.get('/', include_in_schema=False)
    def index():
        return FileResponse(static / 'index.html')

    @app.get('/health')
    def health():
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return {'status':'ok'}

    @app.get('/api/state')
    def state():
        with Session(engine) as s, s.begin():
            return snapshot(s)

    @app.post('/api/commands')
    def command(cmd: Command):
        with Session(engine) as s, s.begin():
            return execute(s, cmd)

    return app


app = create_app()
