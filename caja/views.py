from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.core.paginator import Paginator
from django.utils import timezone
from decimal import Decimal, InvalidOperation
from .models import Caja
from ventas.models import Ventas, Pago, DevolucionVenta
from usuarios.utils import requiere_perfil

def _sumar(queryset, campo):
    return queryset.aggregate(total=Sum(campo))['total'] or Decimal('0.00')


def _resumen_caja(caja):
    ventas = Ventas.objects.filter(id_caja=caja)
    devoluciones = DevolucionVenta.objects.filter(caja=caja)
    pagos = Pago.objects.filter(id_caja=caja)

    ventas_efectivo = _sumar(
        ventas.filter(estado_de_pago='pagado', metodo_pago='efectivo'),
        'total',
    ) - _sumar(devoluciones.filter(metodo_reintegro='efectivo'), 'monto')
    ventas_transferencia = _sumar(
        ventas.filter(estado_de_pago='pagado', metodo_pago='transferencia'),
        'total',
    ) - _sumar(devoluciones.filter(metodo_reintegro='transferencia'), 'monto')
    ventas_fiado = _sumar(
        ventas.filter(estado_de_pago='fiado'),
        'total',
    ) - _sumar(devoluciones.filter(metodo_reintegro='fiado'), 'monto')

    return {
        'ventas_efectivo': ventas_efectivo,
        'ventas_transferencia': ventas_transferencia,
        'ventas_fiado': ventas_fiado,
        'cobros_efectivo': _sumar(pagos.filter(metodo_pago='efectivo'), 'monto'),
        'cobros_transferencia': _sumar(pagos.filter(metodo_pago='transferencia'), 'monto'),
    }

@login_required
def gestion_caja(request):
    caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()
    page_obj = Paginator(
        Caja.objects.all().order_by('-fecha_apertura', '-id_caja'),
        7,
    ).get_page(request.GET.get('page'))

    ventas_efectivo = Decimal('0.00')
    ventas_transferencia = Decimal('0.00')
    ventas_fiado = Decimal('0.00')
    cobros_efectivo = Decimal('0.00')
    cobros_transferencia = Decimal('0.00')
    monto_esperado = Decimal('0.00')

    if caja_activa:
        resumen = _resumen_caja(caja_activa)
        ventas_efectivo = resumen['ventas_efectivo']
        ventas_transferencia = resumen['ventas_transferencia']
        ventas_fiado = resumen['ventas_fiado']
        cobros_efectivo = resumen['cobros_efectivo']
        cobros_transferencia = resumen['cobros_transferencia']

        monto_esperado = caja_activa.monto_inicial + ventas_efectivo + cobros_efectivo

    return render(request, 'caja/gestion_caja.html', {
        'caja_activa': caja_activa,
        'ventas_efectivo': ventas_efectivo,
        'ventas_transferencia': ventas_transferencia,
        'ventas_fiado': ventas_fiado,
        'cobros_efectivo': cobros_efectivo,
        'cobros_transferencia': cobros_transferencia,
        'monto_esperado': monto_esperado,
        'historial_cajas': page_obj,
        'page_obj': page_obj,
    })

@login_required
@requiere_perfil('Administrador', 'Vendedor')
def abrir_caja(request):
    if request.method == 'POST':
        if Caja.objects.filter(estado='abierta').exists():
            messages.warning(request, 'Ya existe una caja abierta en el sistema.')
            return redirect('gestion_caja')

        try:
            monto_inicial = Decimal(request.POST.get('monto_inicial', '0.00'))
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, 'El monto inicial debe ser un número válido igual o mayor que cero.')
            return redirect('gestion_caja')

        if not monto_inicial.is_finite() or monto_inicial < 0:
            messages.error(request, 'El monto inicial no puede ser negativo.')
            return redirect('gestion_caja')

        Caja.objects.create(
            usuario=request.user,
            monto_inicial=monto_inicial,
            estado='abierta'
        )

        messages.success(request, f'Caja abierta con un monto inicial de ${monto_inicial:.2f}')

    return redirect('gestion_caja')

@login_required
@requiere_perfil('Administrador', 'Vendedor')
def cerrar_caja(request):
    if request.method == 'POST':
        caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()

        if not caja_activa:
            messages.error(request, 'No hay ninguna caja abierta para cerrar.')
            return redirect('gestion_caja')

        try:
            monto_real = Decimal(request.POST.get('monto_real', '0.00'))
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, 'El efectivo contado debe ser un número válido igual o mayor que cero.')
            return redirect('gestion_caja')

        if not monto_real.is_finite() or monto_real < 0:
            messages.error(request, 'El efectivo contado no puede ser negativo.')
            return redirect('gestion_caja')

        observaciones = request.POST.get('observaciones', '').strip()

        resumen = _resumen_caja(caja_activa)
        monto_esperado = (
            caja_activa.monto_inicial
            + resumen['ventas_efectivo']
            + resumen['cobros_efectivo']
        )
        diferencia = monto_real - monto_esperado

        caja_activa.monto_final_esperado = monto_esperado
        caja_activa.monto_final_real = monto_real
        caja_activa.diferencia = diferencia
        caja_activa.fecha_cierre = timezone.now()
        caja_activa.estado = 'cerrada'
        caja_activa.observaciones = observaciones
        caja_activa.save()

        if diferencia == Decimal('0.00'):
            messages.success(request, 'Caja cerrada correctamente sin diferencias.')
        elif diferencia > Decimal('0.00'):
            messages.info(request, f'Caja cerrada con Sobrante de ${diferencia:.2f}.')
        else:
            messages.error(request, f'Caja cerrada con Faltante de ${abs(diferencia):.2f}.')

    return redirect('gestion_caja')

@login_required
@requiere_perfil('Administrador')
def forzar_cierre_caja(request, id_caja):
    if request.method == 'POST':
        caja = get_object_or_404(Caja, pk=id_caja, estado='abierta')
        
        resumen = _resumen_caja(caja)
        monto_esperado = (
            caja.monto_inicial
            + resumen['ventas_efectivo']
            + resumen['cobros_efectivo']
        )
        
        caja.monto_final_esperado = monto_esperado
        caja.monto_final_real = monto_esperado 
        caja.diferencia = Decimal('0.00')
        caja.fecha_cierre = timezone.now()
        caja.estado = 'cerrada'
        caja.observaciones = f"Cierre forzado por el Administrador {request.user.username}. (Caja original de {caja.usuario.username})"
        caja.save()
        
        messages.warning(request, f'Se forzó el cierre de la caja #{caja.id_caja} que pertenecía a {caja.usuario.username}.')
    return redirect('gestion_caja')