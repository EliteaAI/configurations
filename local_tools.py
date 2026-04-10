

try:
    from pylon.core.tools import log
    log = log
except ImportError:
    class log:
        def __getattr__(self, item):
            return print

try:
    from ..shared.tools.api_tools import APIBase
    APIBase = APIBase
except ImportError:
    log.warning('Error importing APIBase')
    raise

from ..shared.tools.config_pydantic import TheConfig
config = TheConfig()

try:
    from ..shared.tools import db
    db = db
except:
    log.warn(f'Error importing db')
    # Fallback: minimal db object with SQLAlchemy engine and Base using TheConfig
    from sqlalchemy import create_engine
    from sqlalchemy.orm import declarative_base, sessionmaker
    class DummyDB:
        engine = create_engine(config.DATABASE_URI, **getattr(config, 'DATABASE_ENGINE_OPTIONS', {}))
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base = declarative_base()
        def make_session(self):
            return self.SessionLocal()
    db = DummyDB()
    # Do not raise here, allow fallback to work

from ..shared.tools.serialize import serialize
serialize = serialize



rpc_manager = None
try:
    from tools import context
    rpc_manager = context.rpc_manager
except ImportError:
    from ..shared.tools.rpc_tools import RpcMixin
    rpc_manager = RpcMixin().rpc


event_manager = None
try:
    from tools import context
    event_manager = context.event_manager
except ImportError:
    from ..shared.tools.rpc_tools import EventManagerMixin
    event_manager = EventManagerMixin().event_manager



from ..shared.tools.secret_field import store_secrets, purge_secrets
store_secrets = store_secrets
purge_secrets = purge_secrets

from ..shared.tools.vault_tools import VaultClient
VaultClient = VaultClient

from tools import auth
current_user = auth.current_user
auth = auth

from tools import register_openapi
register_openapi = register_openapi
