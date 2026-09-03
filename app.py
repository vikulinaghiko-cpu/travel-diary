from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from http.cookies import SimpleCookie
from html import escape
from pathlib import Path
from datetime import datetime, timedelta
import sqlite3, hashlib, secrets, os, argparse, base64

BASE_DIR=Path(__file__).resolve().parent
DB_PATH=BASE_DIR/'travel.db'
STATIC_DIR=BASE_DIR/'static'


def now_iso(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def hash_password(password,salt=None):
    salt=salt or secrets.token_hex(16);d=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),150000).hex();return f'{salt}${d}'
def verify_password(password,stored):
    try:salt,d=stored.split('$',1)
    except ValueError:return False
    t=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),150000).hex();return secrets.compare_digest(t,d)
def db():
    c=sqlite3.connect(DB_PATH);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def demo_image(label,bg='#0f766e'):
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="900" height="540"><rect width="100%" height="100%" fill="{bg}"/><circle cx="720" cy="120" r="65" fill="#fde68a"/><path d="M0 410 L190 230 L330 365 L500 180 L760 410 Z" fill="#d1fae5" opacity=".9"/><path d="M0 430 L250 330 L420 450 L650 300 L900 430 L900 540 L0 540Z" fill="#134e4a"/><text x="55" y="95" font-family="Arial" font-size="42" fill="white">{label}</text></svg>'''
    return 'data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()

def init_db():
    c=db();c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,display_name TEXT NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,expires_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS trips(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,description TEXT NOT NULL,location_name TEXT NOT NULL,latitude REAL,longitude REAL,image_data TEXT,cost REAL NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    ''')
    if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0:
        c.executemany('INSERT INTO users(username,display_name,password_hash,created_at) VALUES(?,?,?,?)',[
            ('traveler','Алексей Морозов',hash_password('travel123'),now_iso()),('marina','Марина Волкова',hash_password('marina123'),now_iso())])
        ids={r['username']:r['id'] for r in c.execute('SELECT id,username FROM users')}
        c.executemany('''INSERT INTO trips(user_id,title,description,location_name,latitude,longitude,image_data,cost,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',[
            (ids['traveler'],'Выходные в Казани','За два дня удалось прогуляться по Кремлю, набережной и Старо-Татарской слободе. Маршрут удобно проходить пешком, а основные точки находятся недалеко друг от друга.','Казань, Россия',55.796127,49.106414,demo_image('Казань','#2563eb'),18500,now_iso(),now_iso()),
            (ids['marina'],'Поездка в Суздаль','Небольшое путешествие с прогулками по историческому центру. Больше всего понравились спокойный ритм города, архитектура и виды у реки.','Суздаль, Россия',56.419836,40.449457,demo_image('Суздаль','#a16207'),12600,now_iso(),now_iso())])
    c.commit();c.close()

def page(title,body,user=None):
    nav=f'<span class="user-chip">{escape(user["display_name"])}</span><a href="/my">Мои путешествия</a><a class="btn small" href="/trip/new">Добавить</a><a href="/logout">Выйти</a>' if user else '<a href="/login">Войти</a><a class="btn small" href="/register">Регистрация</a>'
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} — TravelNote</title><link rel="stylesheet" href="/static/style.css"></head><body><header><div class="wrap nav"><a class="brand" href="/">Travel<span>Note</span></a><nav><a href="/">Лента путешествий</a>{nav}</nav></div></header><main class="wrap">{body}</main><footer><div class="wrap">Учебное приложение «Дневник путешествий»</div></footer></body></html>'''
def redirect(h,loc,cookie=None):
    h.send_response(303);h.send_header('Location',loc)
    if cookie:h.send_header('Set-Cookie',cookie)
    h.end_headers()
def money(x):return f'{float(x):,.0f} ₽'.replace(',',' ')

def card(t):
    img=t['image_data'] or demo_image('Travel')
    return f'''<article class="trip-card"><a class="trip-image" href="/trip/{t['id']}" style="background-image:url('{img}')"></a><div class="trip-content"><div class="meta">{escape(t['display_name'])} · {escape(t['created_at'][:10])}</div><h2><a href="/trip/{t['id']}">{escape(t['title'])}</a></h2><p class="place">⌖ {escape(t['location_name'])}</p><p>{escape(t['description'][:170])}{'…' if len(t['description'])>170 else ''}</p><strong>{money(t['cost'])}</strong></div></article>'''

class Handler(BaseHTTPRequestHandler):
    server_version='TravelNote/1.0'
    def log_message(self,fmt,*args):print('[travel]',fmt%args)
    def send_html(self,h,status=200):
        d=h.encode();self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(d)));self.end_headers();self.wfile.write(d)
    def parse_post(self):
        n=int(self.headers.get('Content-Length','0') or 0);d=self.rfile.read(n).decode(errors='replace');p=parse_qs(d,keep_blank_values=True);return{k:v[-1] for k,v in p.items()}
    def user(self,c):
        ck=SimpleCookie(self.headers.get('Cookie',''))
        if 'session' not in ck:return None
        return c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at>?',(ck['session'].value,now_iso())).fetchone()
    def do_GET(self):
        parsed=urlparse(self.path);path=parsed.path
        if path.startswith('/static/'):
            fp=STATIC_DIR/path[len('/static/'):]
            if fp.exists():
                d=fp.read_bytes();self.send_response(200);self.send_header('Content-Type','text/css');self.send_header('Content-Length',str(len(d)));self.end_headers();self.wfile.write(d);return
            self.send_error(404);return
        c=db();u=self.user(c)
        try:
            if path=='/':
                trips=c.execute('''SELECT t.*,u.display_name FROM trips t JOIN users u ON u.id=t.user_id ORDER BY t.created_at DESC''').fetchall();body=f'''<section class="hero"><div><span>Личный опыт в маршрутах</span><h1>Дневник путешествий</h1><p>Записывайте поездки, сохраняйте геопозицию, фотографии и расходы, смотрите истории других пользователей.</p>{'<a class="hero-btn" href="/trip/new">Записать путешествие</a>' if u else '<a class="hero-btn" href="/register">Начать вести дневник</a>'}</div></section><div class="trip-grid">{''.join(card(t) for t in trips)}</div>''';self.send_html(page('Путешествия',body,u));return
            if path=='/register':
                self.send_html(page('Регистрация','''<div class="form-card"><h1>Создание пользователя</h1><form method="post"><label>Логин<input name="username" required minlength="3"></label><label>Имя<input name="display_name" required></label><label>Пароль<input type="password" name="password" minlength="6" required></label><button class="btn">Зарегистрироваться</button></form></div>''',u));return
            if path=='/login':
                self.send_html(page('Вход','''<div class="form-card"><h1>Вход</h1><p class="hint">Демо: traveler / travel123</p><form method="post"><label>Логин<input name="username" required></label><label>Пароль<input type="password" name="password" required></label><button class="btn">Войти</button></form></div>''',u));return
            if path=='/logout':
                ck=SimpleCookie(self.headers.get('Cookie',''));token=ck['session'].value if 'session' in ck else ''
                if token:c.execute('DELETE FROM sessions WHERE token=?',(token,));c.commit()
                redirect(self,'/','session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax');return
            if path=='/trip/new':
                if not u:redirect(self,'/login');return
                self.send_html(page('Новое путешествие',self.trip_form(),u));return
            if path=='/my':
                if not u:redirect(self,'/login');return
                trips=c.execute('SELECT t.*,u.display_name FROM trips t JOIN users u ON u.id=t.user_id WHERE t.user_id=? ORDER BY t.created_at DESC',(u['id'],)).fetchall();self.send_html(page('Мои путешествия',f'<div class="page-head"><h1>Мои путешествия</h1><p>Здесь можно открыть запись, изменить или удалить её.</p></div><div class="trip-grid">{"".join(card(t) for t in trips) or "<div class=empty>Пока нет записей.</div>"}</div>',u));return
            if path.startswith('/trip/') and path.endswith('/edit'):
                if not u:redirect(self,'/login');return
                tid=int(path.split('/')[2]);t=c.execute('SELECT * FROM trips WHERE id=? AND user_id=?',(tid,u['id'])).fetchone()
                if not t:self.send_error(403);return
                self.send_html(page('Редактирование',self.trip_form(t),u));return
            if path.startswith('/trip/'):
                try:tid=int(path.split('/')[2])
                except:self.send_error(404);return
                t=c.execute('SELECT t.*,u.display_name FROM trips t JOIN users u ON u.id=t.user_id WHERE t.id=?',(tid,)).fetchone()
                if not t:self.send_error(404);return
                map_link=''
                if t['latitude'] is not None and t['longitude'] is not None:
                    map_link=f'''<a class="map-link" target="_blank" rel="noopener" href="https://www.openstreetmap.org/?mlat={t['latitude']}&mlon={t['longitude']}#map=13/{t['latitude']}/{t['longitude']}">Открыть точку на карте</a>'''
                actions=''
                if u and u['id']==t['user_id']:
                    actions=f'''<div class="actions"><a class="ghost" href="/trip/{tid}/edit">Редактировать</a><form method="post" action="/trip/{tid}/delete" onsubmit="return confirm('Удалить запись?')"><button class="danger">Удалить</button></form></div>'''
                img=t['image_data'] or demo_image('Travel')
                body=f'''<article class="trip-full"><div class="trip-hero" style="background-image:url('{img}')"></div><div class="trip-main"><div class="meta">{escape(t['display_name'])} · {escape(t['created_at'][:16])}</div><h1>{escape(t['title'])}</h1><div class="trip-facts"><div><span>Местоположение</span><strong>{escape(t['location_name'])}</strong>{map_link}</div><div><span>Координаты</span><strong>{t['latitude'] if t['latitude'] is not None else '—'}, {t['longitude'] if t['longitude'] is not None else '—'}</strong></div><div><span>Стоимость поездки</span><strong>{money(t['cost'])}</strong></div></div><div class="description">{escape(t['description']).replace(chr(10),'<br>')}</div>{actions}</div></article>''';self.send_html(page(t['title'],body,u));return
            self.send_error(404)
        finally:c.close()
    def trip_form(self,t=None):
        g=lambda k:escape(str(t[k])) if t and t[k] is not None else '';action=f'/trip/{t["id"]}/edit' if t else '/trip/new';img=t['image_data'] if t and t['image_data'] else ''
        return f'''<div class="form-card wide"><h1>{'Редактирование путешествия' if t else 'Новое путешествие'}</h1><p class="hint">Дополнительные функции: геопозиция, изображение места и стоимость путешествия.</p><form method="post" action="{action}" id="trip-form"><label>Название<input name="title" value="{g('title')}" required></label><label>Описание<textarea name="description" rows="7" required>{g('description')}</textarea></label><label>Местоположение<input name="location_name" value="{g('location_name')}" placeholder="Москва, Россия" required></label><div class="geo-box"><div class="two"><label>Широта<input id="lat" name="latitude" value="{g('latitude')}" inputmode="decimal"></label><label>Долгота<input id="lon" name="longitude" value="{g('longitude')}" inputmode="decimal"></label></div><button type="button" class="ghost" onclick="getGeo()">Определить мою геопозицию</button><span id="geo-status" class="muted"></span></div><label>Стоимость, ₽<input type="number" min="0" step="0.01" name="cost" value="{g('cost')}" required></label><label>Изображение места<input type="file" id="image-file" accept="image/png,image/jpeg,image/webp,image/svg+xml"></label><input type="hidden" name="image_data" id="image-data" value="{escape(img)}"><div id="preview" class="preview" {'style="background-image:url('+chr(39)+escape(img)+chr(39)+')"' if img else ''}></div><button class="btn">Сохранить путешествие</button></form></div><script>
function getGeo(){{const s=document.getElementById('geo-status');if(!navigator.geolocation){{s.textContent='Геолокация не поддерживается браузером';return}}s.textContent='Определяем координаты…';navigator.geolocation.getCurrentPosition(p=>{{document.getElementById('lat').value=p.coords.latitude.toFixed(6);document.getElementById('lon').value=p.coords.longitude.toFixed(6);s.textContent='Координаты получены';}},()=>s.textContent='Не удалось получить геопозицию');}}
const file=document.getElementById('image-file');file.addEventListener('change',()=>{{if(!file.files[0])return;if(file.files[0].size>2*1024*1024){{alert('Для учебной версии выберите изображение до 2 МБ');file.value='';return}}const r=new FileReader();r.onload=e=>{{document.getElementById('image-data').value=e.target.result;document.getElementById('preview').style.backgroundImage=`url('${{e.target.result}}')`;}};r.readAsDataURL(file.files[0]);}});
</script>'''
    def do_POST(self):
        parsed=urlparse(self.path);path=parsed.path;f=self.parse_post();c=db();u=self.user(c)
        try:
            if path=='/register':
                username=f.get('username','').strip().lower();name=f.get('display_name','').strip();pw=f.get('password','')
                try:c.execute('INSERT INTO users(username,display_name,password_hash,created_at) VALUES(?,?,?,?)',(username,name,hash_password(pw),now_iso()));c.commit();redirect(self,'/login')
                except sqlite3.IntegrityError:self.send_html(page('Ошибка','<div class="notice">Такой логин уже используется.</div>'))
                return
            if path=='/login':
                r=c.execute('SELECT * FROM users WHERE username=?',(f.get('username','').strip().lower(),)).fetchone()
                if not r or not verify_password(f.get('password',''),r['password_hash']):self.send_html(page('Вход','<div class="notice">Неверный логин или пароль.</div>'));return
                token=secrets.token_urlsafe(32);exp=(datetime.now()+timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S');c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,r['id'],exp));c.commit();redirect(self,'/',f'session={token}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax');return
            if not u:redirect(self,'/login');return
            if path=='/trip/new' or (path.startswith('/trip/') and path.endswith('/edit')):
                lat=self.num(f.get('latitude'));lon=self.num(f.get('longitude'));cost=self.num(f.get('cost')) or 0;image=f.get('image_data','')
                if len(image)>3_000_000:image=''
                vals=(f.get('title','').strip(),f.get('description','').strip(),f.get('location_name','').strip(),lat,lon,image,cost,now_iso())
                if path=='/trip/new':cur=c.execute('INSERT INTO trips(user_id,title,description,location_name,latitude,longitude,image_data,cost,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(u['id'],)+vals[:-1]+(now_iso(),now_iso()));tid=cur.lastrowid
                else:
                    tid=int(path.split('/')[2]);c.execute('UPDATE trips SET title=?,description=?,location_name=?,latitude=?,longitude=?,image_data=?,cost=?,updated_at=? WHERE id=? AND user_id=?',vals+(tid,u['id']))
                c.commit();redirect(self,f'/trip/{tid}');return
            if path.startswith('/trip/') and path.endswith('/delete'):
                tid=int(path.split('/')[2]);c.execute('DELETE FROM trips WHERE id=? AND user_id=?',(tid,u['id']));c.commit();redirect(self,'/my');return
            self.send_error(404)
        finally:c.close()
    def num(self,v):
        try:return float(str(v).replace(',','.'))
        except:return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=int(os.getenv('PORT','8003')));args=ap.parse_args();init_db();print(f'TravelNote: http://127.0.0.1:{args.port}');ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
if __name__=='__main__':main()
