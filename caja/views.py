from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_POST
from decimal import Decimal
from .models import Caja
from ventas.models import Ventas, Pago, DevolucionVenta
from usuarios.utils import leer_monto, requiere_perfil, _tiene_perfil
from auditoria.notificaciones import horas_alerta_caja_abierta, verificar_cajas_abiertas

# Largo mínimo del motivo de un cierre forzado.
MOTIVO_MINIMO = 10

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


def _monto_esperado(caja, resumen):
    """Total que debería haber al cierre: monto inicial + ventas y cobros
    en efectivo + ventas y cobros por transferencia (netos de devoluciones).
    Las ventas fiadas no suman porque todavía no ingresó dinero."""
    return (
        caja.monto_inicial
        + resumen['ventas_efectivo']
        + resumen['cobros_efectivo']
        + resumen['ventas_transferencia']
        + resumen['cobros_transferencia']
    )

@login_required
def gestion_caja(request):
    caja_activa = Caja.objects.filter(estado='abierta', usuario=request.user).last()
    solo_forzados = request.GET.get('forzados') == '1'
    historial = Caja.objects.select_related('usuario', 'cerrada_por').order_by('-fecha_apertura', '-id_caja')
    if solo_forzados:
        historial = historial.filter(cierre_forzado=True)
    page_obj = Paginator(historial, 7).get_page(request.GET.get('page'))

    # Cajas abiertas por otros usuarios (bloquean la apertura de una nueva).
    verificar_cajas_abiertas()
    es_admin = _tiene_perfil(request.user, 'Administrador')
    horas_alerta = horas_alerta_caja_abierta()
    ahora = timezone.now()
    cajas_de_otros = []
    for caja in Caja.objects.select_related('usuario').filter(estado='abierta').exclude(usuario=request.user):
        horas = (ahora - caja.fecha_apertura).total_seconds() / 3600
        cajas_de_otros.append({'caja': caja, 'horas': int(horas), 'demorada': horas >= horas_alerta})

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

        monto_esperado = _monto_esperado(caja_activa, resumen)

    return render(request, 'caja/gestion_caja.html', {
        'caja_activa': caja_activa,
        'ventas_efectivo': ventas_efectivo,
        'ventas_transferencia': ventas_transferencia,
        'ventas_fiado': ventas_fiado,
        'cobros_efectivo': cobros_efectivo,
        'cobros_transferencia': cobros_transferencia,
        'total_transferencias': ventas_transferencia + cobros_transferencia,
        'monto_esperado': monto_esperado,
        'historial_cajas': page_obj,
        'page_obj': page_obj,
        'solo_forzados': solo_forzados,
        'cajas_de_otros': cajas_de_otros,
        'es_admin': es_admin,
        'horas_alerta': horas_alerta,
        'motivo_minimo': MOTIVO_MINIMO,
    })

def _hay_caja_abierta():
    return Caja.objects.filter(estado='abierta').exists()


@login_required
@requiere_perfil('Administrador', 'Vendedor')
def abrir_caja(request):
    if request.method == 'POST':
        if _hay_caja_abierta():
            messages.warning(request, 'Ya existe una caja abierta en el sistema.')
            return redirect('gestion_caja')

        monto_inicial, error = leer_monto(request.POST.get('monto_inicial'), 'El monto inicial')
        if error:
            messages.error(request, error)
            return redirect('gestion_caja')

        caja = Caja.objects.create(
            usuario=request.user,
            monto_inicial=monto_inicial,
            estado='abierta'
        )

        # Si dos personas abrieron caja en el mismo instante, ambas pasaron el
        # control de arriba. Ya confirmadas las dos, se queda la primera y la
        # otra se descarta (recién creada, todavía no tiene movimientos).
        primera = Caja.objects.filter(estado='abierta').order_by('pk').first()
        if primera.pk != caja.pk:
            caja.delete()
            messages.warning(request, 'Ya existe una caja abierta en el sistema.')
            return redirect('gestion_caja')

        messages.success(request, f'Caja abierta con un monto inicial de ${monto_inicial:.2f}')

    return redirect('gestion_caja')

@login_required
@requiere_perfil('Administrador', 'Vendedor')
def cerrar_caja(request):
    if request.method == 'POST':
        monto_real, error = leer_monto(request.POST.get('monto_real'), 'El monto contado')
        if error:
            messages.error(request, error)
            return redirect('gestion_caja')

        observaciones = request.POST.get('observaciones', '').strip()

        with transaction.atomic():
            # Bloquea la caja: si un administrador la está cerrando de forma
            # forzada al mismo tiempo, uno espera al otro y no se pisan.
            caja_activa = Caja.objects.select_for_update().filter(
                estado='abierta', usuario=request.user,
            ).order_by('pk').last()

            if not caja_activa:
                messages.error(request, 'No hay ninguna caja abierta para cerrar.')
                return redirect('gestion_caja')

            resumen = _resumen_caja(caja_activa)
            monto_esperado = _monto_esperado(caja_activa, resumen)
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
@require_POST
def forzar_cierre_caja(request, id_caja):
    """
    Cierre de una caja que su dueño dejó abierta. El administrador cuenta el
    dinero y escribe el motivo: la diferencia se calcula igual que en un cierre
    normal, así un faltante queda registrado en lugar de ocultarse.
    """
    motivo = ' '.join(request.POST.get('motivo', '').split())
    monto_real, error = leer_monto(request.POST.get('monto_real'), 'El monto contado')

    with transaction.atomic():
        # Bloquea la fila: si el vendedor justo la está cerrando, no se pisan.
        caja = get_object_or_404(Caja.objects.select_for_update().select_related('usuario'), pk=id_caja)
        if caja.estado != 'abierta':
            messages.info(request, f'La caja #{caja.id_caja} ya estaba cerrada.')
            return redirect('gestion_caja')
        if caja.usuario_id == request.user.pk:
            error = 'Para cerrar tu propia caja usá el arqueo normal.'
        elif len(motivo) < MOTIVO_MINIMO:
            error = f'Indicá el motivo del cierre forzado (al menos {MOTIVO_MINIMO} caracteres).'
        if error:
            messages.error(request, error)
            return redirect('gestion_caja')

        resumen = _resumen_caja(caja)
        monto_esperado = _monto_esperado(caja, resumen)
        diferencia = monto_real - monto_esperado

        caja.monto_final_esperado = monto_esperado
        caja.monto_final_real = monto_real
        caja.diferencia = diferencia
        caja.fecha_cierre = timezone.now()
        caja.estado = 'cerrada'
        caja.cierre_forzado = True
        caja.cerrada_por = request.user
        caja.motivo_cierre_forzado = motivo
        caja.observaciones = (
            f'Cierre forzado por {request.user.username} (caja de {caja.usuario.username}). Motivo: {motivo}'
        )
        caja.save()

    texto = f'Se forzó el cierre de la caja #{caja.id_caja} de {caja.usuario.username}. '
    if diferencia == 0:
        messages.warning(request, texto + 'El conteo coincide con lo esperado.')
    elif diferencia > 0:
        messages.warning(request, texto + f'Sobrante de ${diferencia:.2f}.')
    else:
        messages.error(request, texto + f'FALTANTE de ${abs(diferencia):.2f}.')
    return redirect('gestion_caja')