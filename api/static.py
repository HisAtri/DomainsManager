from modules.server import *

@app.route('/<path:filename>')
def serve_static(filename):
    try:
        return send_from_directory(src_path, filename)
    except FileNotFoundError:
        return None

__all__ = ['app', 'v1_bp', 'logger', 'src_path']
