import json
from flask import jsonify, request, Response, redirect, flash, render_template
from functools import wraps
from sqlalchemy import or_, event
from werkzeug.security import check_password_hash

from app import app, cache
from app.api import update, getEventEntities, setEventData, getEventConnections
from app.models import Entity, Edit, Category, Revenue, Expense, Fundingconnection, Dataconnection, \
    Collaboration, Employment, Relation
from database import db
from config import ADMIN_NAME, ADMIN_HASH, FLASK_SESSION_SECRET_KEY

GET_CATEGORIES_PATH = '/api/categories'
GET_CONNECTIONS_PATH = '/api/connections'
GET_ENTITIES_PATH = '/api/entities'
SAVE_PATH = '/api/save'
DELETE_PATH = '/api/delete'

def cache_key_prefix(path):
    return 'view/' + path

@event.listens_for(Category, 'after_insert')
@event.listens_for(Category, 'after_update')
@event.listens_for(Category, 'after_delete')
def receive_after_category_event(mapper, category, target):
    print("receive_after_category_event for %(target)s" % { 'target': target })
    cache.delete(cache_key_prefix(GET_CATEGORIES_PATH))
    cache.delete(cache_key_prefix(GET_CONNECTIONS_PATH))
    cache.delete(cache_key_prefix(GET_ENTITIES_PATH))

@event.listens_for(Entity, 'after_insert')
@event.listens_for(Entity, 'after_update')
@event.listens_for(Entity, 'after_delete')
def receive_after_entity_event(mapper, connection, target):
    print("receive_after_entity_event for %(target)s" % { 'target': target })
    cache.delete(cache_key_prefix(GET_CONNECTIONS_PATH))
    cache.delete(cache_key_prefix(GET_ENTITIES_PATH))

def check_auth(username, password):
    return username == ADMIN_NAME and check_password_hash(ADMIN_HASH, password)


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)

    return decorated


@app.route(GET_ENTITIES_PATH, methods=['GET'])
@cache.cached(key_prefix=cache_key_prefix(GET_ENTITIES_PATH))
def get_entities():
    if 'Event-Name' in request.headers:
        if 'Event-Data-Only' in request.headers:
            return jsonify(nodes=getEventEntities(request.headers['Event-Name']))
        else:
            return jsonify(nodes=(nodes() + getEventEntities(request.headers['Event-Name'])))

    return jsonify(nodes=nodes())

@app.route(GET_CONNECTIONS_PATH)
@cache.cached(key_prefix=cache_key_prefix(GET_CONNECTIONS_PATH))
def get_connections():
    data = connections()
    if 'Event-Name' in request.headers:
        if 'Event-Data-Only' in request.headers:
            # app.logger.debug('event-data-ran')
            data = getEventConnections(request.headers['Event-Name'])
            # app.logger.debug(data)
            return jsonify(connections=data)
        else:
            event = getEventConnections(request.headers['Event-Name'])
            returnData = {}
            for key, value in data.iteritems():
                if key not in event:
                    returnData[key] = value
                else:
                    returnData[key] = value + event[key]
            return jsonify(connections = returnData)
    return jsonify(connections=data)

def save_event_data(request):
    eventName = request.headers['Event-Name']
    data = json.loads(request.data)['entity']
    # app.logger.debug(data)
    setEventData(eventName, data)

def connections():
    return {
        'Funding': funding_connections(),
        'Data': data_connections(),
        'Collaboration': collaboration_connections(),
        'Employment': employment_connections(),
        'Relation': relation_connections()
    }


@app.route(GET_CATEGORIES_PATH)
@cache.cached(key_prefix=cache_key_prefix(GET_CATEGORIES_PATH))
def categories():
    return jsonify(categories=[category.json() for category in Category.query.all()])


def nodes():
    return Entity.all_as_json()


def edits():
    d = {}
    for edit in Edit.query.all():
        d.setdefault(edit.entity_id, []).append(edit.json())
    return d


def funding_connections():
    # Watch out for IDs/indexes: http://stackoverflow.com/a/16824896
    return [{'source': f.giver_id, 'target': f.receiver_id} for f in Fundingconnection.query.all()]


def data_connections():
    return [{'source': d.giver_id, 'target': d.receiver_id} for d in Dataconnection.query.all()]


def collaboration_connections():
    return [{'source': c.entity_id1, 'target': c.entity_id2} for c in Collaboration.query.all()]


def employment_connections():
    return [{'source': e.entity_id1, 'target': e.entity_id2} for e in Employment.query.all()]


def relation_connections():
    return [{'source': r.entity_id1, 'target': r.entity_id2} for r in Relation.query.all()]

@app.route(SAVE_PATH, methods=['POST'])
def save():
    # app.logger.debug(request.data)
    jsonData = json.loads(request.data)

    if 'Event-Name' in request.headers:
        save_event_data(request)
        if 'optOut' in jsonData and jsonData['optOut']:
            return get_entities()

    entity = None
    data = jsonData['entity']
    data["ip"] = request.remote_addr
    data["edit_type"] = None

    if data['id']:
        entity = Entity.query.get(data['id'])
    elif data['name']:
        data["edit_type"] = "create"
        entity = Entity(data['name'])
        db.add(entity)
        db.commit()

    if entity:
        if not data["edit_type"]:
            data["edit_type"] = "update"
        update(entity, data)

    return get_entities()

@app.route(DELETE_PATH, methods=['POST'])
@requires_auth
def delete():
    app.secret_key = FLASK_SESSION_SECRET_KEY
    method = request.form.get('_method')
    id = request.args.get('id')
    if method == 'DELETE':
        db.query(Collaboration) \
            .filter(or_(Collaboration.entity_id1 == id, Collaboration.entity_id2 == id)) \
            .delete(synchronize_session='evaluate')

        db.query(Dataconnection) \
            .filter(or_(Dataconnection.giver_id == id, Dataconnection.receiver_id == id)) \
            .delete(synchronize_session='evaluate')

        db.query(Employment) \
            .filter(or_(Employment.entity_id1 == id, Employment.entity_id2 == id)) \
            .delete(synchronize_session='evaluate')

        db.query(Revenue).filter(Revenue.entity_id == id).delete(synchronize_session='evaluate')

        db.query(Expense).filter(Expense.entity_id == id).delete(synchronize_session='evaluate')

        db.query(Relation) \
            .filter(or_(Relation.entity_id1 == id, Relation.entity_id2 == id)) \
            .delete(synchronize_session='evaluate')

        db.query(Fundingconnection) \
            .filter(or_(Fundingconnection.giver_id == id, Fundingconnection.receiver_id == id)) \
            .delete(synchronize_session='evaluate')

        db.query(Edit).filter(Edit.entity_id == id).delete(synchronize_session='evaluate')

        db.execute("DELETE FROM location_table WHERE entity_id=" + id + ";")
        db.execute("DELETE FROM category_table WHERE entity_id=" + id + ";")
        db.execute("DELETE FROM keypeople_table WHERE entity_id=" + id + ";")

        db.query(Entity).filter(Entity.id == id).delete(synchronize_session='evaluate')

        db.commit()
        flash("Delete was successful")
    return redirect('/admin')


@app.route('/admin', methods=['GET'])
@requires_auth
def admin_login():
    data = {
        'nodes': reversed(nodes()),
        'edits': edits()
    }
    return render_template('admin.html', data=data)
