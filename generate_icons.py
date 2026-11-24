import os, shutil
from PIL import Image

logo_path = r'C:/Ricardo/Python/Crm/static/img/logo-imobicrm.png'
output_dir = r'C:/Ricardo/Python/Crm/static/img'

sizes = {
    'favicon-16x16.png': (16, 16),
    'favicon-32x32.png': (32, 32),
    'apple-touch-icon.png': (180, 180),
    'icon-192x192.png': (192, 192),
    'icon-512x512.png': (512, 512),
}

im = Image.open(logo_path).convert('RGBA')
for name, size in sizes.items():
    img = im.resize(size, Image.LANCZOS)
    img.save(os.path.join(output_dir, name), format='PNG', optimize=True)

# duplicate icons for legacy references
os.makedirs(r'C:/Ricardo/Python/Crm/static/icons', exist_ok=True)
shutil.copyfile(os.path.join(output_dir, 'icon-192x192.png'), r'C:/Ricardo/Python/Crm/static/icons/icon-192.png')
shutil.copyfile(os.path.join(output_dir, 'icon-512x512.png'), r'C:/Ricardo/Python/Crm/static/icons/icon-512.png')
