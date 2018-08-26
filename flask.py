# -*- coding: utf-8 -*-
"""
    flask
    ~~~~~

    A microframework based on Werkzeug.  It's extensively documented
    and follows best practice patterns.

    :copyright: (c) 2010 by Armin Ronacher.
    :license: BSD, see LICENSE for more details.
"""
from __future__ import with_statement
import os
import sys

from threading import local
from jinja2 import Environment, PackageLoader, FileSystemLoader
from werkzeug import Request as RequestBase, Response as ResponseBase, \
     LocalStack, LocalProxy, create_environ, cached_property, \
     SharedDataMiddleware
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import HTTPException, InternalServerError
from werkzeug.contrib.securecookie import SecureCookie

# utilities we import from Werkzeug and Jinja2 that are unused
# in the module but are exported as public interface.
from werkzeug import abort, redirect
from jinja2 import Markup, escape

# use pkg_resource if that works, otherwise fall back to cwd.  The
# current working directory is generally not reliable with the notable
# exception of google appengine.
try:
    import pkg_resources
    pkg_resources.resource_stream
except (ImportError, AttributeError):
    pkg_resources = None


class Request(RequestBase):
    """
    默认发出的网络请求类型，只需使用环境变量environ即可初始化
    """

    def __init__(self, environ):
        RequestBase.__init__(self, environ)
        self.endpoint = None
        self.view_args = None


class Response(ResponseBase):
    """
    默认的网络请求响应的类型
    """
    default_mimetype = 'text/html'


class _RequestGlobals(object):
    """
    存储全局变量的类型
    """
    pass


class _RequestContext(object):
    """
    单次网络请求上下文，包括此次网络请求所有相关的信息

    初始化的参数为flask的application和本次请求的上下文信息。
    application中的url_map保存着注册的路由信息，通过bind_to_environ()方法绑定请求的上下文信息environ，
    后续可以直接调用此url_adapter的match()方法，即可获得本次请求对应的endpoint和请求参数

    _RequestContext利用__enter__与__exit__魔术方法可以与with配合提供上下文管理器，进入请求上下文环境时，
    将当前上下文环境推入上下文环境栈_request_ctx_stack中，
    当离开当前上下文环境时，从上下文环境栈_request_ctx_stack中移除
    """

    def __init__(self, app, environ):
        self.app = app
        self.url_adapter = app.url_map.bind_to_environ(environ)
        self.request = app.request_class(environ)
        self.session = app.open_session(self.request)
        self.g = _RequestGlobals()
        self.flashes = None

    def __enter__(self):
        _request_ctx_stack.push(self)

    def __exit__(self, exc_type, exc_value, tb):
        # do not pop the request stack if we are in debug mode and an
        # exception happened.  This will allow the debugger to still
        # access the request object in the interactive shell.
        if tb is None or not self.app.debug:
            _request_ctx_stack.pop()


def url_for(endpoint, **values):
    """Generates a URL to the given endpoint with the method provided.

    :param endpoint: the endpoint of the URL (name of the function)
    :param values: the variable arguments of the URL rule
    """
    return _request_ctx_stack.top.url_adapter.build(endpoint, values)


def flash(message):
    """Flashes a message to the next request.  In order to remove the
    flashed message from the session and to display it to the user,
    the template has to call :func:`get_flashed_messages`.

    :param message: the message to be flashed.
    """
    session['_flashes'] = (session.get('_flashes', [])) + [message]


def get_flashed_messages():
    """Pulls all flashed messages from the session and returns them.
    Further calls in the same request to the function will return
    the same messages.
    """
    flashes = _request_ctx_stack.top.flashes
    if flashes is None:
        _request_ctx_stack.top.flashes = flashes = \
            session.pop('_flashes', [])
    return flashes


def render_template(template_name, **context):
    """Renders a template from the template folder with the given
    context.

    :param template_name: the name of the template to be rendered
    :param context: the variables that should be available in the
                    context of the template.
    """
    current_app.update_template_context(context)
    return current_app.jinja_env.get_template(template_name).render(context)


def render_template_string(source, **context):
    """Renders a template from the given template source string
    with the given context.

    :param template_name: the sourcecode of the template to be
                          rendered
    :param context: the variables that should be available in the
                    context of the template.
    """
    current_app.update_template_context(context)
    return current_app.jinja_env.from_string(source).render(context)


def _default_template_ctx_processor():
    """Default template context processor.  Injects `request`,
    `session` and `g`.
    """
    reqctx = _request_ctx_stack.top
    return dict(
        request=reqctx.request,
        session=reqctx.session,
        g=reqctx.g
    )


def _get_package_path(name):
    """Returns the path to a package or cwd if that cannot be found."""
    try:
        return os.path.abspath(os.path.dirname(sys.modules[name].__file__))
    except (KeyError, AttributeError):
        return os.getcwd()


class Flask(object):
    """The flask object implements a WSGI application and acts as the central
    object.  It is passed the name of the module or package of the
    application.  Once it is created it will act as a central registry for
    the view functions, the URL rules, template configuration and much more.

    The name of the package is used to resolve resources from inside the
    package or the folder the module is contained in depending on if the
    package parameter resolves to an actual python package (a folder with
    an `__init__.py` file inside) or a standard module (just a `.py` file).

    For more information about resource loading, see :func:`open_resource`.

    Usually you create a :class:`Flask` instance in your main module or
    in the `__init__.py` file of your package like this::

        from flask import Flask
        app = Flask(__name__)
    """

    #: the class that is used for request objects.  See :class:`~flask.request`
    #: for more information.
    request_class = Request

    #: the class that is used for response objects.  See
    #: :class:`~flask.Response` for more information.
    response_class = Response

    #: path for the static files.  If you don't want to use static files
    #: you can set this value to `None` in which case no URL rule is added
    #: and the development server will no longer serve any static files.
    static_path = '/static'

    #: if a secret key is set, cryptographic components can use this to
    #: sign cookies and other things.  Set this to a complex random value
    #: when you want to use the secure cookie for instance.
    secret_key = None

    #: The secure cookie uses this for the name of the session cookie
    session_cookie_name = 'session'

    #: options that are passed directly to the Jinja2 environment
    jinja_options = dict(
        autoescape=True,
        extensions=['jinja2.ext.autoescape', 'jinja2.ext.with_']
    )

    def __init__(self, package_name):
        #: the debug flag.  Set this to `True` to enable debugging of
        #: the application.  In debug mode the debugger will kick in
        #: when an unhandled exception ocurrs and the integrated server
        #: will automatically reload the application if changes in the
        #: code are detected.
        self.debug = False

        #: the name of the package or module.  Do not change this once
        #: it was set by the constructor.
        self.package_name = package_name

        #: where is the app root located?
        self.root_path = _get_package_path(self.package_name)

        #: a dictionary of all view functions registered.  The keys will
        #: be function names which are also used to generate URLs and
        #: the values are the function objects themselves.
        #: to register a view function, use the :meth:`route` decorator.
        self.view_functions = {}

        #: a dictionary of all registered error handlers.  The key is
        #: be the error code as integer, the value the function that
        #: should handle that error.
        #: To register a error handler, use the :meth:`errorhandler`
        #: decorator.
        self.error_handlers = {}

        #: a list of functions that should be called at the beginning
        #: of the request before request dispatching kicks in.  This
        #: can for example be used to open database connections or
        #: getting hold of the currently logged in user.
        #: To register a function here, use the :meth:`before_request`
        #: decorator.
        self.before_request_funcs = []

        #: a list of functions that are called at the end of the
        #: request.  Tha function is passed the current response
        #: object and modify it in place or replace it.
        #: To register a function here use the :meth:`after_request`
        #: decorator.
        self.after_request_funcs = []

        #: a list of functions that are called without arguments
        #: to populate the template context.  Each returns a dictionary
        #: that the template context is updated with.
        #: To register a function here, use the :meth:`context_processor`
        #: decorator.
        self.template_context_processors = [_default_template_ctx_processor]

        self.url_map = Map()

        if self.static_path is not None:
            self.url_map.add(Rule(self.static_path + '/<filename>',
                                  build_only=True, endpoint='static'))
            if pkg_resources is not None:
                target = (self.package_name, 'static')
            else:
                target = os.path.join(self.root_path, 'static')
            self.wsgi_app = SharedDataMiddleware(self.wsgi_app, {
                self.static_path: target
            })

        #: the Jinja2 environment.  It is created from the
        #: :attr:`jinja_options` and the loader that is returned
        #: by the :meth:`create_jinja_loader` function.
        self.jinja_env = Environment(loader=self.create_jinja_loader(),
                                     **self.jinja_options)
        self.jinja_env.globals.update(
            url_for=url_for,
            get_flashed_messages=get_flashed_messages
        )

    def create_jinja_loader(self):
        """Creates the Jinja loader.  By default just a package loader for
        the configured package is returned that looks up templates in the
        `templates` folder.  To add other loaders it's possible to
        override this method.
        """
        if pkg_resources is None:
            return FileSystemLoader(os.path.join(self.root_path, 'templates'))
        return PackageLoader(self.package_name)

    def update_template_context(self, context):
        """Update the template context with some commonly used variables.
        This injects request, session and g into the template context.

        :param context: the context as a dictionary that is updated in place
                        to add extra variables.
        """
        reqctx = _request_ctx_stack.top
        for func in self.template_context_processors:
            context.update(func())

    def run(self, host='localhost', port=5000, **options):
        """
        应用启动调用的方法，通过调用werkzeug的run_simple() 注册当前application为请求处理函数。
        后续收到网络请求时由调用当前application的__call__进行处理
        """
        from werkzeug import run_simple
        if 'debug' in options:
            self.debug = options.pop('debug')
        options.setdefault('use_reloader', self.debug)
        options.setdefault('use_debugger', self.debug)
        return run_simple(host, port, self, **options)

    def test_client(self):
        """Creates a test client for this application.  For information
        about unit testing head over to :ref:`testing`.
        """
        from werkzeug import Client
        return Client(self, self.response_class, use_cookies=True)

    def open_resource(self, resource):
        """Opens a resource from the application's resource folder.  To see
        how this works, consider the following folder structure::

            /myapplication.py
            /schemal.sql
            /static
                /style.css
            /template
                /layout.html
                /index.html

        If you want to open the `schema.sql` file you would do the
        following::

            with app.open_resource('schema.sql') as f:
                contents = f.read()
                do_something_with(contents)

        :param resource: the name of the resource.  To access resources within
                         subfolders use forward slashes as separator.
        """
        if pkg_resources is None:
            return open(os.path.join(self.root_path, resource), 'rb')
        return pkg_resources.resource_stream(self.package_name, resource)

    def open_session(self, request):
        """
        获取请求对应的session数据，当没有session时，可以创建请求对应的session信息
        创建的前提是需要配置了secret_key
        """
        key = self.secret_key
        if key is not None:
            return SecureCookie.load_cookie(request, self.session_cookie_name,
                                            secret_key=key)

    def save_session(self, session, response):
        """
        更新session信息
        """
        if session is not None:
            session.save_cookie(response, self.session_cookie_name)

    def add_url_rule(self, rule, endpoint, **options):
        """
        利用werkzeug提供的Map和Rule类型存储rule和endpoint之间的映射关系
        后续当网络请求到达时，与Map中的数据进行匹配，匹配的即可得到endpoint，通过endpoint即可查找view_functions得到处理函数

        可以看到看到需要将endpoint和methods构建Rule，然后调用Map的add方法，即可保存路由映射关系
        """
        options['endpoint'] = endpoint
        options.setdefault('methods', ('GET',))
        self.url_map.add(Rule(rule, **options))

    def route(self, rule, **options):
        """
        route装饰器，调用add_url_rule() 方法注册url对应的处理函数
        使用处理函数__name__作为注册的end_point, 同时将处理函数的end_point与函数的映射存储在view_functions中

        在实际查找路由时，通过url找到匹配的end_point，然后再通过查找view_functions即可找到对应的处理函数
        """
        def decorator(f):
            self.add_url_rule(rule, f.__name__, **options)
            self.view_functions[f.__name__] = f
            return f
        return decorator

    def errorhandler(self, code):
        """
        用于注册错误处理函数，在执行网络请求失败时可以进行处理
        """
        def decorator(f):
            self.error_handlers[code] = f
            return f
        return decorator

    def before_request(self, f):
        """
        注册网络请求处理前的回调函数
        """
        self.before_request_funcs.append(f)
        return f

    def after_request(self, f):
        """
        注册网络请求处理后的回调函数
        """
        self.after_request_funcs.append(f)
        return f

    def context_processor(self, f):
        """Registers a template context processor function."""
        self.template_context_processors.append(f)
        return f

    def match_request(self):
        """
        获取本次请求匹配的endpoint和请求的参数
        从请求栈_request_ctx_stack取出栈顶的请求，可以认为是本次的网络请求，利用请求的url和参数，和路由注册中保存的数据进行匹配，
        即可得到endpoint和请求参数
        """
        rv = _request_ctx_stack.top.url_adapter.match()
        request.endpoint, request.view_args = rv
        return rv

    def dispatch_request(self):
        """
        真正处理本次网络请求的方法
        根据本次请求进行匹配，获得本次请求对应的endpoint和请求参数，查找view_functions得到处理函数，执行后得到处理结果
        """
        try:
            endpoint, values = self.match_request()
            return self.view_functions[endpoint](**values)
        except HTTPException, e:
            # 处理失败时可以调用注册的错误处理函数进行处理
            handler = self.error_handlers.get(e.code)
            if handler is None:
                return e
            return handler(e)
        except Exception, e:
            handler = self.error_handlers.get(500)
            if self.debug or handler is None:
                raise
            return handler(e)

    def make_response(self, rv):
        """
        将网络请求的响应转换为Response类型，保证返回数据的都是特定的格式
        """
        if isinstance(rv, self.response_class):
            return rv
        if isinstance(rv, basestring):
            return self.response_class(rv)
        if isinstance(rv, tuple):
            return self.response_class(*rv)
        return self.response_class.force_type(rv, request.environ)

    def preprocess_request(self):
        """
        在实际的网络请求前执行回调函数
        将before_request_funcs内注册的回调函数依次执行，前面的回调函数可以阻止后面回调函数的执行
        用户可以通过before_request()方法注册回调函数到before_request_funcs中
        """
        for func in self.before_request_funcs:
            rv = func()
            if rv is not None:
                return rv

    def process_response(self, response):
        """
        网络请求后的处理，保存请求的session，同时执行注册的回调函数
        将after_request_funcs内注册的回调函数依次执行
        用户可以通过after_request()方法注册回调函数到after_request_funcs中
        """
        session = _request_ctx_stack.top.session
        if session is not None:
            self.save_session(session, response)
        for handler in self.after_request_funcs:
            response = handler(response)
        return response

    def wsgi_app(self, environ, start_response):
        """
        实际处理网络请求，执行请求处理，并将返回值转换为Response类型。
        在执行前调用before_request_funcs内的回调函数， 在执行后执行after_request_funcs中的回调函数
        """
        with self.request_context(environ):
            rv = self.preprocess_request()
            if rv is None:
                rv = self.dispatch_request()
            response = self.make_response(rv)
            response = self.process_response(response)
            return response(environ, start_response)

    def request_context(self, environ):
        """
        创建请求上下文环境
        """
        return _RequestContext(self, environ)

    def test_request_context(self, *args, **kwargs):
        """Creates a WSGI environment from the given values (see
        :func:`werkzeug.create_environ` for more information, this
        function accepts the same arguments).
        """
        return self.request_context(create_environ(*args, **kwargs))

    def __call__(self, environ, start_response):
        """
        实际处理网络请求的方法，符合wsgi框架的设计，当有网络请求时, 将wsgi Application 作为可调用对象，处理网络请求
        :param environ: 传入的环境变量
        :param start_response: 传入的回调函数，在返回前调用此回调函数
        """
        return self.wsgi_app(environ, start_response)


# 存储上下文的栈结构，每个请求的上下文信息保存的类型为 _RequestContext
_request_ctx_stack = LocalStack()
# 获取最新请求上下文的一些属性信息，利用LocalProxy保证拿到的始终是最新的上下文信息的属性信息
current_app = LocalProxy(lambda: _request_ctx_stack.top.app)  # 本次请求对应的application
request = LocalProxy(lambda: _request_ctx_stack.top.request)  # 本次网络请求，类型为Request
session = LocalProxy(lambda: _request_ctx_stack.top.session)  # 本次网络请求对应的session数据
g = LocalProxy(lambda: _request_ctx_stack.top.g)   # 本次网络请求对应的全局变量, 类型为 _RequestGlobals