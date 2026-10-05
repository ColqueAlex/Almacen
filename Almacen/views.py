from io import BytesIO
from django.http import HttpResponse
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import json
from django.db.models import ProtectedError
from decimal import Decimal
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from Almacen.models import Productos, Proveedor, Usuario, Perfil, Ventas, DetallesVentas
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.db.models import Max, Sum, Q
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth import logout
from django.contrib.auth import authenticate, login
from .models import Productos, Proveedor, Compras, DetallesCompra, LotesProducto, Ventas, Clientes
from datetime import date, datetime



@login_required
def home(request):
    if not request.user.activo:
        logout(request)
        messages.error(request, 'Tu cuenta ha sido dada de baja.')
        return redirect('login')

    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')
        
    return render(request, "principal.html")



@login_required
def consultar(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')
        
    productos = Productos.objects.all().order_by('nombre')
    busqueda = request.GET.get('busqueda', '').strip()

    if busqueda:
        productos = productos.filter(nombre__icontains=busqueda)

    has_inactive_products = productos.filter(estado='inactivo').exists()

    return render(request, "productos.html", {
        'productos': productos,
        'busqueda': busqueda,
        'has_inactive_products': has_inactive_products,
    })


@login_required
def guardar(request):
    if request.method == 'POST':
        nombre = request.POST.get("nombre", "").strip()
        categoria = request.POST.get("categoria", "").strip()
        marca = (request.POST.get("marca") or "").strip() or "Sin marca"
        precio = request.POST.get("precio")
        estado = request.POST.get("estado", "activo")

        if Productos.objects.filter(nombre__iexact=nombre, marca__iexact=marca).exists():
            messages.error(request, f'Ya existe el producto "{nombre}" de marca "{marca}" en el catálogo.')
            return redirect('consultar')

        p = Productos(
            nombre=nombre,
            categoria=categoria,
            marca=marca,
            precio=precio,
            estado=estado,
        )
        try:
            p.full_clean()
            p.save()
            messages.success(request, f'Producto "{nombre}" agregado al catálogo.')
        except ValidationError as e:
            if hasattr(e, 'message_dict') and 'categoria' in e.message_dict:
                messages.error(request, 'La categoría no puede contener números.')
            else:
                messages.error(request, 'Verifique que el precio sea mayor a 0.')
    
    return redirect('consultar')

@login_required
def cambiar_estado(request, id):
    producto = get_object_or_404(Productos, pk=id)
    producto.estado = 'inactivo' if producto.estado == 'activo' else 'activo'
    producto.save()
    messages.success(request, f'Estado del producto actualizado a {producto.get_estado_display()}')
    return redirect('consultar')


@login_required
def ventas(request):
    if request.user.debe_cambiar_clave:
        return redirect('cambiar_clave')

    hoy = timezone.now().date()
    periodo = request.GET.get('filtro', 'todos')

    ventas_qs = Ventas.objects.prefetch_related('detalles__producto').order_by('-fecha', '-hora')

    if periodo == 'semana':
        desde = hoy - timezone.timedelta(days=7)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)
    elif periodo == 'mes':
        desde = hoy - timezone.timedelta(days=30)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)
    elif periodo == 'año':
        desde = hoy - timezone.timedelta(days=365)
        ventas_qs = ventas_qs.filter(fecha__gte=desde)

    fecha_limite = hoy.replace(year=hoy.year - 1)
    return render(request, 'Gstion_Ventas.html', {
        'ventas': ventas_qs,
        'fecha_limite': fecha_limite,
        'periodo': periodo,
    })


@login_required
def registrar_venta(request):
    if request.method == 'POST':
        try:
            cart_data = request.POST.get('cart_data', '[]')
            fiado = request.POST.get('fiado') == 'true'
            id_cliente_post = request.POST.get('id_cliente')
            items = json.loads(cart_data)

            if not items:
                messages.error(request, 'El carrito está vacío.')
                return redirect('registrar_venta')

            # Si es fiado
            id_cliente_final = int(id_cliente_post) if (fiado and id_cliente_post) else 0

            with transaction.atomic():
                estado_pago = 'Fiado' if fiado else 'Pagado'
                ahora = datetime.now()

                ultimo_id_venta = Ventas.objects.aggregate(Max('id_ventas'))['id_ventas__max'] or 0
                nuevo_id_venta = ultimo_id_venta + 1

                venta = Ventas.objects.create(
                    id_ventas=nuevo_id_venta,
                    id_caja=1,
                    total=Decimal('0.00'),
                    fecha=date.today(),
                    hora=ahora.time(),
                    id_clientes=id_cliente_final,
                    estado_de_pago=estado_pago
                )

                total_venta = Decimal('0.00')

                ultimo_id_detalle = DetallesVentas.objects.aggregate(Max('id_detalles_ventas'))['id_detalles_ventas__max'] or 0

                for item in items:
                    producto = get_object_or_404(Productos, pk=item['id'])
                    cantidad_necesaria = int(item['cantidad'])
                    precio_unitario = Decimal(str(item['precio']))

                    if producto.stock < cantidad_necesaria:
                        raise ValueError(f'Stock insuficiente para "{producto.nombre}". Disponible: {producto.stock}')

                    subtotal = cantidad_necesaria * precio_unitario

                    ultimo_id_detalle += 1

                    DetallesVentas.objects.create(
                        id_detalles_ventas=ultimo_id_detalle,
                        venta=venta,
                        producto=producto,
                        cantidad=cantidad_necesaria,
                        precio_unitario_compra=precio_unitario,
                        subtotal=subtotal
                    )

                    lotes = LotesProducto.objects.filter(
                        id_producto=producto,
                        activo=True,
                        stock_actual__gt=0
                    ).order_by('fecha_vencimiento', 'id')

                    pendiente = cantidad_necesaria
                    for lote in lotes:
                        if pendiente <= 0:
                            break

                        if lote.stock_actual >= pendiente:
                            lote.stock_actual -= pendiente
                            pendiente = 0
                        else:
                            pendiente -= lote.stock_actual
                            lote.stock_actual = 0
                        
                        lote.save()

                    total_venta += subtotal

                venta.total = total_venta
                venta.save()

            messages.success(request, f'Venta #{venta.id_ventas} registrada exitosamente.')
            return redirect('ventas')

        except Exception as e:
            messages.error(request, f'Error al registrar la venta: {str(e)}')
            return redirect('registrar_venta')

    # GET: Cargar productos, categorías y clientes ordenados por nombre
    productos = Productos.objects.filter(estado='activo')
    categorias = Productos.objects.filter(estado='activo').values_list('categoria', flat=True).distinct()
    clientes = Clientes.objects.all().order_by('nombre')

    return render(request, 'Registro_ventas.html', {
        'productos': productos,
        'categorias': categorias,
        'clientes': clientes
    })


@login_required
def eliminar_venta(request, id_venta):
    if request.method != 'POST':
        return redirect('ventas')

    venta = get_object_or_404(Ventas, pk=id_venta)
    hace_un_año = timezone.now().date().replace(year=timezone.now().date().year - 1)

    if venta.fecha > hace_un_año:
        messages.error(request, f'La venta #{id_venta} no puede eliminarse: tiene menos de 1 año de antigüedad.')
        return redirect('ventas')

    venta.detalles.all().delete()
    venta.delete()
    messages.success(request, f'Venta #{id_venta} y sus detalles eliminados correctamente.')
    return redirect('ventas')


@login_required
def cambiar_estado_venta(request, id_venta):
    if request.method != 'POST':
        return redirect('ventas')

    venta = get_object_or_404(Ventas, pk=id_venta)
    nuevo_estado = request.POST.get('estado_de_pago', '').strip()
    estados_validos = ['pagado', 'fiado']

    if nuevo_estado not in estados_validos:
        messages.error(request, 'Estado de pago no válido.')
        return redirect('ventas')

    venta.estado_de_pago = nuevo_estado
    venta.save()
    messages.success(request, f'Venta #{id_venta} actualizada a "{nuevo_estado.capitalize()}".')
    return redirect('ventas')


@login_required
def detalle(request, id):
    producto = Productos.objects.get(pk=id)
    return render(request, "productoEditar.html", {
        'producto': producto
    })


@login_required
def eliminar_seleccionados(request):
    if request.method != 'POST':
        return redirect('consultar')

    ids_raw = request.POST.get('selected_ids', '')
    ids = [item.strip() for item in ids_raw.split(',') if item.strip()]

    if not ids:
        messages.warning(request, 'No hay productos marcados para eliminar.')
        return redirect('consultar')

    productos = Productos.objects.filter(id__in=ids, estado='inactivo')

    if not productos.exists():
        messages.warning(request, 'Solo se pueden eliminar productos inactivos.')
        return redirect('consultar')

    eliminados = 0
    bloqueados = []

    for producto in productos:
        try:
            producto.delete()
            eliminados += 1
        except ProtectedError:
            bloqueados.append(producto.nombre)

    if eliminados:
        messages.success(request, f'Se eliminaron {eliminados} producto(s) correctamente.')

    if bloqueados:
        nombres = ', '.join(bloqueados)
        messages.error(
            request,
            f'No se pudo eliminar: {nombres}. '
            f'{"Este producto tiene" if len(bloqueados) == 1 else "Estos productos tienen"} '
            f'ventas registradas asociadas y no pueden borrarse.'
        )

    return redirect('consultar')


@login_required
def editar(request):
    if request.method == 'POST':
        id_prod = request.POST.get("id")
        producto = get_object_or_404(Productos, pk=id_prod)

        producto.nombre = request.POST.get("nombre", "").strip()
        producto.categoria = request.POST.get("categoria", "").strip()
        producto.marca = (request.POST.get("marca") or "").strip() or "Sin marca"
        producto.precio = request.POST.get("precio")
        producto.estado = request.POST.get("estado", "activo")

        try:
            producto.full_clean()
            producto.save()
            messages.success(request, f'Producto "{producto.nombre}" actualizado correctamente.')
        except ValidationError as e:
            if hasattr(e, 'message_dict') and 'categoria' in e.message_dict:
                messages.error(request, 'La categoría no puede contener números.')
            else:
                messages.error(request, 'Error al actualizar: Verifique que el precio sea válido.')
            return redirect('detalle', id=id_prod)

    return redirect('consultar')

@login_required
def baja_usuario(request, dni):
    usuario_destino = get_object_or_404(Usuario, dni=dni)
    usuario_actual = request.user

    if usuario_actual.dni == usuario_destino.dni:
        messages.error(request, 'No puedes darte de baja a ti mismo.')
        return redirect('listar_usuarios')

    rol_actual = usuario_actual.id_perfil.nombre if usuario_actual.id_perfil else ''
    rol_destino = usuario_destino.id_perfil.nombre if usuario_destino.id_perfil else ''

    if rol_actual not in ['Administrador', 'Propietario']:
        messages.error(request, 'No tienes permisos para dar de baja a ningún usuario.')
        return redirect('listar_usuarios')

    if rol_actual == rol_destino:
        messages.error(request, f'No puedes dar de baja a otro usuario con el mismo rango ({rol_actual}).')
        return redirect('listar_usuarios')

    usuario_destino.activo = False
    usuario_destino.fecha_baja = timezone.now()
    usuario_destino.save()
    messages.success(request, f'Usuario {usuario_destino.username} dado de baja correctamente.')
    return redirect('listar_usuarios')


@login_required
def editar_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    if request.method == 'POST':
        usuario.correo = request.POST.get('correo')
        perfil_id = request.POST.get('perfil')
        usuario.id_perfil = Perfil.objects.filter(id=perfil_id).first() if perfil_id else None
        usuario.save()
        messages.success(request, 'Usuario actualizado correctamente.')
        return redirect('listar_usuarios')

    perfiles = Perfil.objects.all()
    return render(request, 'usuario_form.html', {
        'usuario': usuario,
        'perfiles': perfiles,
        'es_edicion': True,
    })


@login_required
def listar_usuarios(request):
    usuarios = Usuario.objects.all()
    return render(request, 'usuarios_list.html', {'usuarios': usuarios})


@login_required
def crear_usuario(request):
    if request.method == 'POST':
        dni = request.POST.get('dni')
        apellido = (request.POST.get('apellido') or '').strip()
        nombre = (request.POST.get('nombre') or '').strip()
        correo = (request.POST.get('correo') or '').strip()
        perfil_id = request.POST.get('perfil')

        if Usuario.objects.filter(dni=dni).exists():
            messages.error(request, f'Ya existe un usuario registrado con el DNI {dni}.')
            perfiles = Perfil.objects.all()
            return render(request, 'usuario_form.html', {
                'perfiles': perfiles,
                'es_edicion': False,
                'dni': dni,
                'apellido': apellido,
                'nombre': nombre,
                'correo': correo,
                'perfil_id': perfil_id,
            })

        password = request.POST.get('password') or str(dni)
        perfil = Perfil.objects.filter(id=perfil_id).first() if perfil_id else None

        usuario = Usuario.objects.create_user(
            dni=dni,
            apellido=apellido,
            nombre=nombre,
            correo=correo,
            password=password,
            id_perfil=perfil,
        )

        usuario.debe_cambiar_clave = True
        usuario.save()

        messages.success(request, f'Usuario {usuario.username} creado exitosamente.')
        return redirect('listar_usuarios')

    perfiles = Perfil.objects.all()
    return render(request, 'usuario_form.html', {'perfiles': perfiles, 'es_edicion': False})

@login_required
def cambiar_clave(request):
    if request.method == 'POST':
        nueva_clave = request.POST.get('nueva_clave')
        confirmar_clave = request.POST.get('confirmar_clave')

        if not nueva_clave or not confirmar_clave:
            return render(request, 'cambiar_clave.html', {'error': 'La contrase?a no puede estar vac?a'})

        if nueva_clave != confirmar_clave:
            return render(request, 'cambiar_clave.html', {'error': 'Las contrase?as no coinciden'})

        request.user.set_password(nueva_clave)
        request.user.debe_cambiar_clave = False
        request.user.save()

        update_session_auth_hash(request, request.user)

        messages.success(request, 'Contrase?a actualizada con ?xito.')
        return redirect('consultar')

    return render(request, 'cambiar_clave.html')

@login_required
def restablecer_clave(request, dni):
    if request.method == 'POST':
        usuario = get_object_or_404(Usuario, dni=dni)
        clave_temporal = str(usuario.dni)
        usuario.set_password(str(usuario.dni))
        usuario.debe_cambiar_clave = True
        usuario.save()
        messages.success(
            request, 
            f'Clave restablecida para {usuario.username}. Nueva clave temporal: {clave_temporal}'
        )
    return redirect('listar_usuarios')

@login_required
def alta_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    usuario.activo = True
    usuario.fecha_baja = None
    usuario.save()
    messages.success(request, f'Usuario {usuario.username} reactivado correctamente.')
    return redirect('listar_usuarios')

def lista_ventas(request):
    filtro = request.GET.get('filtro', 'todos')
    ventas = Ventas.objects.all().order_by('-fecha', '-hora')
    hoy = timezone.now().date()

    if filtro == 'semana':
        hace_una_semana = hoy - timedelta(days=7)
        ventas = ventas.filter(fecha__gte=hace_una_semana)

    elif filtro == 'mes':
        hace_un_mes = hoy - timedelta(days=30)
        ventas = ventas.filter(fecha__gte=hace_un_mes)

    elif filtro == 'anio':
        ventas = ventas.filter(fecha__year=hoy.year)

    context = {'ventas': ventas, 'filtro_activo': filtro}
    return render(request, 'tu_app/ventas.html', context)

def login_usuario(request):
    if request.user.is_authenticated:
        return redirect('inicio')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        user_exists = Usuario.objects.filter(username=username).first()

        if user_exists and not user_exists.activo:
            messages.error(request, 'Cuenta inactiva.')
            return render(request, 'login.html')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            if user.debe_cambiar_clave:
                return redirect('cambiar_clave')
            return redirect('inicio')
        else:
            messages.error(request, 'Usuario o contraseña incorrectos.')
            return render(request, 'login.html')

    return render(request, 'login.html')

@login_required
def listar_proveedores(request):
    proveedores = Proveedor.objects.all().order_by('nombre')
    return render(request, 'proveedores_list.html', {'proveedores': proveedores})


@login_required
def crear_proveedor(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        tipo_productos = request.POST.get('tipo_productos', '').strip()

        if Proveedor.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, f'Ya existe un proveedor registrado con el nombre "{nombre}".')
            return render(request, 'proveedor_form.html', {'es_edicion': False})

        Proveedor.objects.create(
            nombre=nombre,
            numero_telefono=telefono,
            tipo_productos=tipo_productos,
            activo=True
        )
        messages.success(request, f'Proveedor "{nombre}" registrado correctamente.')
        return redirect('listar_proveedores')

    return render(request, 'proveedor_form.html', {'es_edicion': False})


@login_required
def editar_proveedor(request, id):
    proveedor = get_object_or_404(Proveedor, pk=id)
    if request.method == 'POST':
        proveedor.nombre = request.POST.get('nombre', '').strip()
        proveedor.numero_telefono = request.POST.get('telefono', '').strip()
        proveedor.tipo_productos = request.POST.get('tipo_productos', '').strip()
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor.nombre}" actualizado correctamente.')
        return redirect('listar_proveedores')

    return render(request, 'proveedor_form.html', {'proveedor': proveedor, 'es_edicion': True})


@login_required
def baja_proveedor(request, id):
    if request.method == 'POST':
        proveedor = get_object_or_404(Proveedor, pk=id)
        proveedor.activo = False
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor.nombre}" dado de baja.')
    return redirect('listar_proveedores')


@login_required
def alta_proveedor(request, id):
    if request.method == 'POST':
        proveedor = get_object_or_404(Proveedor, pk=id)
        proveedor.activo = True
        proveedor.save()
        messages.success(request, f'Proveedor "{proveedor.nombre}" reactivado correctamente.')
    return redirect('listar_proveedores')

@login_required
def registro_compras(request):
    if request.method == 'POST':
        try:
            cart_data = request.POST.get('cart_data', '[]')
            proveedor_id = request.POST.get('id_proveedor')
            items = json.loads(cart_data)

            if not items:
                messages.error(request, 'Debe agregar al menos un producto a la compra.')
                return redirect('registrar_compra')

            proveedor = None
            if proveedor_id:
                proveedor = Proveedor.objects.filter(pk=proveedor_id).first()

            with transaction.atomic():
                compra = Compras.objects.create(
                    id_proveedor=proveedor,
                    id_usuario=request.user,
                    total=Decimal('0.00')
                )

                total_compra = Decimal('0.00')

                for item in items:
                    producto = get_object_or_404(Productos, pk=item['id_producto'])
                    cantidad = int(item['cantidad'])
                    precio_costo = Decimal(str(item['precio_costo']))
                    lote_str = item.get('lote', 'Sin lote').strip()
                    fecha_venc = item.get('fecha_vencimiento') or None
                    subtotal = cantidad * precio_costo

                    DetallesCompra.objects.create(
                        id_compra=compra,
                        id_producto=producto,
                        lote=lote_str,
                        fecha_vencimiento=fecha_venc,
                        cantidad=cantidad,
                        precio_unitario_compra=precio_costo,
                        subtotal=subtotal
                    )

                    lote_obj, created = LotesProducto.objects.get_or_create(
                        id_producto=producto,
                        lote=lote_str,
                        fecha_vencimiento=fecha_venc,
                        defaults={'precio_costo': precio_costo, 'stock_actual': 0}
                    )
                    lote_obj.stock_actual += cantidad
                    lote_obj.save()

                    total_compra += subtotal

                compra.total = total_compra
                compra.save()

            messages.success(request, f'Compra #{compra.id} registrada exitosamente. Stock actualizado.')
            return redirect('listar_compras')

        except Exception as e:
            messages.error(request, f'Error al procesar la compra: {str(e)}')
            return redirect('registrar_compra')

    proveedores = Proveedor.objects.filter(activo=True).order_by('nombre')
    productos = Productos.objects.filter(estado='activo').order_by('nombre')
    return render(request, 'registro_compras.html', {
        'proveedores': proveedores,
        'productos': productos
    })


@login_required
def listar_compras(request):
    compras = Compras.objects.all().order_by('-id')
    return render(request, 'compras_list.html', {'compras': compras})

@login_required
def anular_venta(request, id_venta):
    venta = get_object_or_404(Ventas, pk=id_venta)

    if venta.estado_de_pago == 'Anulado':
        messages.warning(request, f'La Venta #{venta.id_ventas} ya se encuentra anulada.')
        return redirect('ventas')

    try:
        with transaction.atomic():
            venta.estado_de_pago = 'Anulado'
            venta.save()

            detalles = DetallesVentas.objects.filter(venta=venta)

            for detalle in detalles:
                producto = detalle.producto
                cantidad_a_devolver = detalle.cantidad

                lote = LotesProducto.objects.filter(
                    id_producto=producto,
                    activo=True
                ).order_by('-fecha_vencimiento', '-id').first()

                if lote:
                    lote.stock_actual += cantidad_a_devolver
                    lote.save()
                else:
                    LotesProducto.objects.create(
                        id_producto=producto,
                        lote='LOTE-DEVOLUCION',
                        precio_costo=producto.precio,
                        stock_actual=cantidad_a_devolver,
                        activo=True
                    )

        messages.success(request, f'Venta #{venta.id_ventas} anulada exitosamente y stock reincorporado al almacén.')

    except Exception as e:
        messages.error(request, f'Error al anular la venta: {str(e)}')

    return redirect('ventas')

@login_required
def generar_comprobante_pdf(request, id_venta):
    venta = get_object_or_404(Ventas, pk=id_venta)
    detalles = DetallesVentas.objects.filter(venta=venta)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    styles = getSampleStyleSheet()

    style_title = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=16, alignment=1, spaceAfter=10)
    style_sub = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10, alignment=1, spaceAfter=20)
    style_body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, spaceAfter=5)

    # Cabecera del ticket
    story.append(Paragraph("<b>ALMACÉN EL REFUGIO</b>", style_title))
    story.append(Paragraph(f"Comprobante de Venta #{venta.id_ventas}<br/>Fecha: {venta.fecha.strftime('%d/%m/%Y')} - Hora: {venta.hora.strftime('%H:%M')}", style_sub))
    story.append(Paragraph(f"<b>Estado de Pago:</b> {venta.estado_de_pago.title()}", style_body))
    story.append(Spacer(1, 10))

    data = [["Producto", "Cant.", "P. Unitario", "Subtotal"]]
    for detalle in detalles:
        data.append([
            detalle.producto.nombre,
            str(detalle.cantidad),
            f"${detalle.precio_unitario_compra:.2f}",
            f"${detalle.subtotal:.2f}"
        ])

    data.append(["", "", "TOTAL:", f"${venta.total:.2f}"])

    table = Table(data, colWidths=[250, 60, 100, 100])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.lightgrey),
        ('FONTNAME', (2, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (2, -1), (-1, -1), colors.HexColor('#f8f9fa')),
    ]))

    story.append(table)
    story.append(Spacer(1, 20))
    story.append(Paragraph("¡Gracias por su compra!", style_sub))

    doc.build(story)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Comprobante_Venta_{venta.id_ventas}.pdf"'
    return response

@login_required
def cuentas_clientes(request):
    clientes = Clientes.objects.all()
    lista_cuentas = []

    for cliente in clientes:
        ventas_pendientes = Ventas.objects.filter(
            id_clientes=cliente.id_clientes
        ).filter(
            Q(estado_de_pago__iexact='fiado') | 
            Q(estado_de_pago__iexact='pendiente') |
            Q(estado_de_pago__iexact='abono_parcial') |
            Q(estado_de_pago__iexact='abono parcial')
        ).order_by('fecha', 'hora')

        saldo_total = ventas_pendientes.aggregate(total=Sum('total'))['total'] or 0.0

        lista_cuentas.append({
            'cliente': cliente,
            'saldo_total': saldo_total,
            'ventas_pendientes': ventas_pendientes,
            'cantidad_ventas': ventas_pendientes.count()
        })

    return render(request, 'cuentas_clientes.html', {'cuentas': lista_cuentas})


@login_required
def registrar_pago_cliente(request, id_cliente):
    if request.method == 'POST':
        cliente = get_object_or_404(Clientes, pk=id_cliente)
        monto_pago = float(request.POST.get('monto', 0))

        if monto_pago <= 0:
            messages.error(request, 'El monto a abonar debe ser mayor a 0.')
            return redirect('cuentas_clientes')

        try:
            with transaction.atomic():
                ventas_pendientes = Ventas.objects.filter(
                    id_clientes=cliente.id_clientes
                ).filter(
                    Q(estado_de_pago__iexact='fiado') | 
                    Q(estado_de_pago__iexact='pendiente') |
                    Q(estado_de_pago__iexact='abono_parcial') |
                    Q(estado_de_pago__iexact='abono parcial')
                ).order_by('fecha', 'hora')

                monto_restante = monto_pago

                for venta in ventas_pendientes:
                    if monto_restante <= 0:
                        break

                    venta_total = float(venta.total)

                    if monto_restante >= venta_total:
                        monto_restante -= venta_total
                        venta.estado_de_pago = 'pagado'
                        venta.save()
                    else:
                        venta.estado_de_pago = 'abono_parcial'
                        venta.save()
                        monto_restante = 0

                messages.success(request, f'Se registró el pago de ${monto_pago:.2f} para {cliente.nombre}.')

        except Exception as e:
            messages.error(request, f'Error al registrar el pago: {str(e)}')

    return redirect('cuentas_clientes')

@login_required
def crear_cliente(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        telefono = request.POST.get('telefono', '').strip()

        if not nombre:
            messages.error(request, 'El nombre del cliente es obligatorio.')
            return redirect('cuentas_clientes')

        cliente = Clientes.objects.create(
            nombre=nombre,
            apellido=apellido,
            telefono=telefono
        )
        messages.success(request, f'Cliente "{cliente.nombre} {cliente.apellido or ""}" registrado con éxito.')
    
    return redirect('cuentas_clientes')