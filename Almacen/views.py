from django.shortcuts import render, redirect
from Almacen.models import Productos, Proveedor
from django.contrib import messages

# Create your views here.
def home(request) :
    return render(request, "principal.html")

def consultar(request) :
    productos = Productos.objects.all()
    return render(request, "productos.html", {
        'productos' : productos
    })

def guardar(request):
    nombre = request.POST["nombre"]
    precio = request.POST["precio"]
    stock = request.POST["stock"]
    descripcion = request.POST.get("descripcion","")
    p = Productos(nombre = nombre, precio = precio, stock = stock, descripcion = descripcion)
    p.save()
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
    precio = request.POST["precio"]
    stock = request.POST["stock"]
    descripcion = request.POST.get("descripcion","")
    id = request.POST["id"]
    Productos.objects.filter(pk = id).update(id=id, nombre=nombre, precio=precio, stock=stock, descripcion=descripcion)
    messages.success(request, 'Producto Actualizado')
    return redirect('consultar')