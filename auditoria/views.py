from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from .models import Auditoria
from usuarios.utils import requiere_perfil

@login_required
@requiere_perfil('Administrador')
def listar_auditoria(request):
    registros = Auditoria.objects.all().order_by('-fecha_hora')
    
    page_obj = Paginator(registros, 20).get_page(request.GET.get('page'))
    
    return render(request, 'auditoria/auditoria.html', {
        'registros': page_obj,
        'page_obj': page_obj,
    })