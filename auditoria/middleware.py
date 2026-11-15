import threading

_thread_locals = threading.local()

def get_current_user():
    return getattr(_thread_locals, 'user', None)

class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.user = request.user
        try:
            response = self.get_response(request)
        finally:
            # Limpiar para que el usuario no "quede pegado" al hilo en el próximo request
            _thread_locals.user = None
        return response