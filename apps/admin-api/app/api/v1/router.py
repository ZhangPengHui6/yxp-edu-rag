from fastapi import APIRouter

from app.api.v1 import (
    annotation,
    auth,
    dashboard,
    evaluation,
    health,
    iam,
    ingestion,
    knowledge_points,
    publish,
    questions,
    review,
    textbooks,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(ingestion.router)
api_router.include_router(textbooks.router)
api_router.include_router(questions.router)
api_router.include_router(knowledge_points.router)
api_router.include_router(annotation.router)
api_router.include_router(review.router)
api_router.include_router(publish.router)
api_router.include_router(evaluation.router)
api_router.include_router(iam.router)
