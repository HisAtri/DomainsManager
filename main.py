import logging

from waitress import serve

from modules.server import *
from db import db

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('')
    logger.info("正在启动...")
    app.register_blueprint(v1_bp)
    serve(app, host='*', port=7920, threads=16, channel_timeout=30)
