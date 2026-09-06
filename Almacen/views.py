from django.shortcuts import render, redirect
from Almacen.models import Productos, Proveedor
from django.contrib import messages
from django.core.exceptions import ValidationError

# Create your views here.
def home(request) :
    return render(request, "principal.html")

def consultar(request) :
    productos = Productos.objects.all()
    stock_filtro = request.GET.get('stock_filtro', 'todos')
    busqueda = request.GET.get('busqueda', '').strip()

    if busqueda:
        productos = productos.filter(nombre__icontains=busqueda)

    if stock_filtro == 'cero':
        productos = productos.filter(stock=0)
    elif stock_filtro == 'hasta_diez':
        productos = productos.filter(stock__gt=0, stock__lte=10)
    elif stock_filtro == 'mayor_diez':
        productos = productos.filter(stock__gt=10)

    return render(request, "productos.html", {
        'productos' : productos,
        'stock_filtro': stock_filtro,
        'busqueda': busqueda
    })

def guardar(request):
    nombre = request.POST["nombre"]
    categoria = request.POST["categoria"]
    marca = request.POST["marca"]
    precio = request.POST["precio"]
    fecha_vencimiento = request.POST.get("fecha_vencimiento")
    stock = request.POST["stock"]
    descripcion = request.POST.get("descripcion","")

    if not fecha_vencimiento:
        messages.error(request, 'La fecha de vencimiento es obligatoria')
        return redirect('consultar')

    if Productos.objects.filter(nombre=nombre).exists():
        messages.error(request, 'Ya existe un producto con ese nombre')
        return redirect('consultar')

    p = Productos(
    nombre=nombre,
    categoria=categoria,
    marca=marca,
    precio=precio,
    fecha_vencimiento=fecha_vencimiento,
    stock=stock,
    descripcion=descripcion
                )
    try:
        p.full_clean()
        p.save()
    except ValidationError:
        messages.error(request, 'El precio y el stock no pueden ser negativos')
        return redirect('consultar')
    messages.success(request, 'Producto Agregado')
    return redirect('consultar')

def eliminar(request, id):
    producto = Productos.objects.filter(pk = id)
    producto.delete()
    messages.success(request, 'Producto eliminado')
    return redirect('consultar')

def detalle(request, id):
    producto = Productos.objects.get(pk = id)
    return render(request, "productoEditar.html", {
        'producto': producto
    })

def editar(request):
    nombre = request.POST["nombre"]
    categoria = request.POST["categoria"]
    marca = request.POST["marca"]
    precio = request.POST["precio"]
    fecha_vencimiento = request.POST.get("fecha_vencimiento") or None
    stock = request.POST["stock"]
    descripcion = request.POST.get("descripcion","")
    id = request.POST["id"]
    producto = Productos.objects.get(pk=id)
    producto.nombre = nombre
    producto.categoria = categoria
    producto.marca = marca
    producto.precio = precio
    producto.fecha_vencimiento = fecha_vencimiento
    producto.stock = stock
    producto.descripcion = descripcion
    try:
        producto.full_clean()
        producto.save()
    except ValidationError:
        messages.error(request, 'El precio y el stock no pueden ser negativos')
        return redirect('consultar')
    messages.success(request, 'Producto Actualizado')
    return redirect('consultar')