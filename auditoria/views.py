from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Auditoria
from usuarios.utils import requiere_perfil

@login_required
@requiere_perfil('Administrador')
def listar_auditoria(request):
    busqueda = request.GET.get('busqueda', '').strip()
    accion = request.GET.get('accion', '').strip()
    modulo = request.GET.get('modulo', '').strip()

    registros = Auditoria.objects.select_related('usuario').all()

    if busqueda:
        busqueda_filtro = (
            Q(usuario__username__icontains=busqueda)
            | Q(modelo__icontains=busqueda)
            | Q(descripcion__icontains=busqueda)
        )
        if 'sistema' in busqueda.casefold():
            busqueda_filtro |= Q(usuario__isnull=True)
        registros = registros.filter(busqueda_filtro)

    acciones_validas = dict(Auditoria.ACCIONES_CHOICES)
    if accion in acciones_validas:
        registros = registros.filter(accion=accion)

    if modulo:
        registros = registros.filter(modelo=modulo)

    registros = registros.order_by('-fecha_hora')
    page_obj = Paginator(registros, 20).get_page(request.GET.get('page'))

    return render(request, 'auditoria/auditoria.html', {
        'registros': page_obj,
        'page_obj': page_obj,
        'busqueda': busqueda,
        'accion_seleccionada': accion,
        'modulo_seleccionado': modulo,
        'acciones': Auditoria.ACCIONES_CHOICES,
        'modulos': Auditoria.objects.order_by('modelo').values_list('modelo', flat=True).distinct(),
    })