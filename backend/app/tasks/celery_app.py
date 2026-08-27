from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "endurance_ai",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.imports"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    broker_connection_retry_on_startup=True,
)
