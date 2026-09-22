@echo off
cd /d "%~dp0"
echo Instalando dependencias...
python -m pip install -r requirements.txt || goto :error
REM Usa Chrome/Edge instalado; esto baja un Chromium de respaldo por si no hay.
python -m playwright install chromium
if not exist config.ini copy config.example.ini config.ini
echo.
echo Listo. Edita config.ini con tus categorias (y claves de WooCommerce si queres actualizacion automatica).
pause
exit /b 0
:error
echo Error: verifica que Python este instalado (https://www.python.org, tildar "Add to PATH").
pause
