
### 3. API 文档（Sphinx 配置指南）

#### 3.1 安装 Sphinx
```bash
uv pip install sphinx sphinx-rtd-theme
```

#### 3.2 初始化 Sphinx 项目
在项目根目录：
```bash
sphinx-quickstart docs
```
按提示设置项目名、作者，选择分离 source/build 目录（推荐）。

#### 3.3 配置 `docs/source/conf.py`
关键修改：
```python
import os
import sys
sys.path.insert(0, os.path.abspath('../..'))   # 确保可导入 drone_rid_spoofer

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',   # 支持 Google/NumPy 风格 docstring
    'sphinx.ext.viewcode',
]

html_theme = 'sphinx_rtd_theme'
```

#### 3.4 生成 API 文档
在 `docs/source/` 下创建 `api.rst`：
```rst
API Reference
=============

.. automodule:: drone_rid_spoofer
   :members:
   :undoc-members:
   :show-inheritance:

Submodules
----------

drone_rid_spoofer.cli
---------------------
.. automodule:: drone_rid_spoofer.cli
   :members:

drone_rid_spoofer.spoofer
-------------------------
.. automodule:: drone_rid_spoofer.spoofer
   :members:

... 为每个模块重复 ...
```

#### 3.5 构建 HTML
```bash
cd docs
make html
```
生成的 HTML 位于 `docs/build/html/`。
