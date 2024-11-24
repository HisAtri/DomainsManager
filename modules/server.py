from flask import Flask, Blueprint, send_from_directory

import logging
import sys
import os


def get_base_path():
    """
    获取程序运行路径
    如果是打包后的exe文件，则返回打包资源路径
    """
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    else:
        return os.getcwd()

app = Flask(__name__, static_folder=None)
logger = logging.getLogger(__name__)
v1_bp = Blueprint('v1', __name__, url_prefix='/api/v1')
v1_bp.config = app.config.copy()

src_path = os.path.join(get_base_path(), 'src')  # 静态资源路径

__all__ = ['app', 'v1_bp', 'logger', 'src_path']
