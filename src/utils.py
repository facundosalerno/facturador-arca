import traceback, io


def format_exception(exception: Exception) -> str:
    "Permite transformar a string excepciones que sucedieron en cualquier momento"
    errorfile = io.StringIO()
    traceback.print_exception(type(exception), exception, exception.__traceback__, file=errorfile)
    return errorfile.getvalue()