from django.urls import path
from . import views

urlpatterns = [
    path('auditoria/', views.listar_auditoria, name='listar_auditoria'),
]