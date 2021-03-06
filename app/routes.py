from app import app, db
from flask import render_template, flash, redirect, request, url_for, jsonify, g
from flask_httpauth import HTTPBasicAuth, HTTPTokenAuth
from flask_login import login_user, logout_user, current_user, login_required
from sqlalchemy import func
from app.models import User, Respuesta, Post
from app.forms import LoginForm, PostForm
from app.email import send_alarm_email
from app.helper import error_response, translate
from werkzeug.urls import url_parse
from datetime import datetime, timedelta
basic_auth = HTTPBasicAuth()
token_auth = HTTPTokenAuth()

state = {'alarma':0}

@app.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user is None or not user.check_password(form.password.data):
            flash('Email o contraseña invalida')
            return redirect(url_for('login'))
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or url_parse(next_page).netloc != '':
            next_page = url_for('dashboard')
        return redirect(next_page)
    return render_template('login.html', title='Sign In', form=form)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    q = Respuesta.query.order_by(Respuesta.id.desc()).first_or_404()
    charts = Respuesta.query.filter(Respuesta.fecha >= (datetime.utcnow()-timedelta(hours=1)), Respuesta.id % 20 == 0)\
            .order_by(Respuesta.id.desc()).all()
    context = { 
    'data': q,
    'conv_tqc':translate['conv_tqc'],
    'conv_tsb':translate['conv_tsb'],
    'conv_status':translate['conv_status'],
    'conv_alarmas':translate['conv_alarmas'],
    'state':state,
    'url':'Dashboard',
    'charts':charts
    }
    return render_template('dashboard.html',  context=context)
    

@app.route('/tables')
@login_required
def tables():
    page = request.args.get('page', 1, type=int)
    data = Respuesta.query.order_by(Respuesta.id.desc()).first_or_404()
    table = db.session.query(Respuesta.grp,func.min(Respuesta.fecha).label("inicio"),\
                        func.max(Respuesta.fecha).label("final"),\
                        func.avg(Respuesta.corriente).label("avgcorriente"),\
                        func.avg(Respuesta.presion).label("avgpresion"))\
                        .filter(Respuesta.encendido == True)\
                        .group_by(Respuesta.grp).order_by(Respuesta.grp.desc())\
                        .paginate(page, app.config['ROWS_PER_TABLE'], False)
    context = { 
    'data': data,
    'table':table,
    'url':'Historial'
    }
    next_url = url_for('tables', page=table.next_num) \
        if table.has_next else None
    prev_url = url_for('tables', page=table.prev_num) \
        if table.has_prev else None
    return render_template('tables.html',  context=context, next_url=next_url,
                           prev_url=prev_url, table=table.items)

@app.route('/agenda', methods=['GET', 'POST'])
@login_required
def agenda():
    page = request.args.get('page', 1, type=int)
    q = Respuesta.query.order_by(Respuesta.id.desc()).first_or_404()
    context = { 
    'data': q,
    'url':'Agenda',
    'page': page
    }
    form = PostForm()
    if  form.validate_on_submit():
        new_post = Post(title = form.title.data, text = form.text.data, type = form.type.data, user = current_user)
        db.session.add(new_post)
        db.session.commit()
        return redirect(url_for("agenda"))
    publi = Post.query.order_by(Post.id.desc()).paginate(page, app.config['POSTS_PER_PAGE'], False)
    next_url = url_for('agenda', page=publi.next_num) \
        if publi.has_next else None
    prev_url = url_for('agenda', page=publi.prev_num) \
        if publi.has_prev else None        
    return render_template('agenda.html',  context=context, form=form, next_url=next_url,
                           prev_url=prev_url, publi=publi.items)


@app.route('/charts')
def charts():
    data = Respuesta.query.order_by(Respuesta.id.desc()).first_or_404()
    charts = Respuesta.query.filter(Respuesta.fecha >= (datetime.utcnow()-timedelta(days=1)), Respuesta.id % 100 == 0)\
             .order_by(Respuesta.id.desc()).all()
    print(charts)
    context = { 
    'data': data,
    'charts':charts,
    'url':'Gráficas'
    }
    return render_template('charts.html',  context=context)
    
'''
    En la siguiente parte se realiza
    todo lo necesario para la API

'''

@basic_auth.verify_password
def verify_password(email, password):
    user = User.query.filter_by(email=email).first()
    if user is None:
        return False
    g.current_user = user
    return user.check_password(password)
    
@basic_auth.error_handler
def basic_auth_error():
    return error_response(401)

@token_auth.verify_token
def verify_token(token):
    g.current_user = User.check_token(token) if token else None
    return g.current_user is not None

@token_auth.error_handler
def token_auth_error():
    return error_response(401)

@app.route('/api/v0/tokens', methods=['POST'])
@basic_auth.login_required
def get_token():
    token = g.current_user.get_token()
    db.session.commit()
    return jsonify({'token': token})

@app.route('/api/v0/send_data', methods=['POST'])
@token_auth.login_required
def send_data():
    data  = request.get_json()
    group = Respuesta.query.order_by(Respuesta.id.desc()).first()
    if group:
        if ( group.encendido == data['encendido']):
            group=group.grp
        else:
            group=group.grp+1
    else:
        group=0
    resp = Respuesta(volt_pe=data['volt_pe'], volt_ky=data['volt_ky'], \
        volt_bat=data['volt_bat'], var_tqc=data['var_tqc'], \
        var_tsb=data['var_tsb'], corriente=data['corriente'], \
        presion=data['presion'], alarma=data['alarma'], \
        encendido=data['encendido'], grp=group )
    db.session.add(resp)
    db.session.commit()
    msg = {'msg':False}
    if 'action' in state.keys():
        msg.update({'msg': state.pop('action')})
    if (data['alarma'] != state['alarma']) and (data['alarma'] != 0):
        print("Acaa")
        user = User.query.filter(User.type == 2).first()
        p = Post(title = "Alarma", text = translate['conv_alarmas'][data['alarma']], type = 'danger', user = user)
        db.session.add(p)
        db.session.commit()
        state['alarma']= data['alarma']
        if app.config['MAIL_ENABLED']:
            u = User.query.filter(User.type==1).all()
            u = [user.email for user in u]
            send_alarm_email(u, translate['conv_alarmas'][data['alarma']])
    return jsonify(msg)

@app.route('/api/v0/switch', methods=['POST'])
@login_required
def switch():
    data  = request.get_json()
    print(data)
    state.update(data)
    status = {'msg':'ok'}
    return jsonify(status)

@app.route('/api/v0/get_data'  , methods=['GET'])
@login_required
def get_data():
    return jsonify(Respuesta.query.order_by(Respuesta.id.desc()).first_or_404().to_dict())