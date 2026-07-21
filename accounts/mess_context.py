import threading

_thread_locals = threading.local()


def set_current_mess(mess):
    _thread_locals.mess = mess


def get_current_mess():
    return getattr(_thread_locals, 'mess', None)


def clear_current_mess():
    _thread_locals.mess = None
