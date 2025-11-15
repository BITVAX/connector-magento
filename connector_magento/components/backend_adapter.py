# Copyright 2017 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import copy
import logging
import socket
from datetime import datetime, date
from urllib.parse import quote_plus

import requests

from odoo.addons.component.core import AbstractComponent
from odoo.addons.connector.exception import NetworkRetryableError, JobError, IDMissingInBackend
from odoo.addons.queue_job.exception import RetryableJobError

_logger = logging.getLogger(__name__)


MAGENTO_DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def serialize_for_json(obj):
    """
    Serializa recursivamente objetos Python date/datetime para JSON.
    Corrige el error: "Object of type date is not JSON serializable"

    :param obj: Objeto a serializar (dict, list, o valor primitivo)
    :return: Objeto serializable en JSON
    """
    if isinstance(obj, dict):
        return {key: serialize_for_json(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [serialize_for_json(item) for item in obj]
    elif isinstance(obj, datetime):
        return obj.strftime(MAGENTO_DATETIME_FORMAT)
    elif isinstance(obj, date):
        return obj.strftime('%Y-%m-%d')
    else:
        return obj


class MagentoLocation(object):
    """Magento 2.0+ connection configuration"""

    def __init__(self, location, token, verify_ssl=True, use_custom_api_path=False):
        self._location = location
        self.token = token
        self.verify_ssl = verify_ssl
        self.use_custom_api_path = use_custom_api_path

        self.use_auth_basic = False
        self.auth_basic_username = None
        self.auth_basic_password = None

    @property
    def location(self):
        location = self._location
        if not self.use_auth_basic:
            return location
        assert self.auth_basic_username and self.auth_basic_password
        replacement = "%s:%s@" % (self.auth_basic_username,
                                  self.auth_basic_password)
        location = location.replace('://', '://' + replacement)
        return location


class Magento2Client(object):
    """Magento 2.0+ REST API Client"""

    def __init__(self, url, token, verify_ssl=True, use_custom_api_path=False):
        if not use_custom_api_path:
            url += '/' if not url.endswith('/') else ''
            url += 'index.php/rest/V1'
        self._url = url
        self._token = token
        self._verify_ssl = verify_ssl

    def call(self, resource_path, arguments, http_method=None, storeview=None):
        if resource_path is None:
            _logger.exception('Magento2 REST API called without resource path')
            raise NotImplementedError
        url = '%s/%s' % (self._url, resource_path)
        if storeview:
            # https://github.com/magento/magento2/issues/3864
            url = url.replace('/rest/V1/', '/rest/%s/V1/' % storeview)
        if http_method is None:
            http_method = 'get'
        function = getattr(requests, http_method)
        headers = {'Authorization': 'Bearer %s' % self._token}
        kwargs = {'headers': headers, 'verify': self._verify_ssl}
        if http_method == 'get':
            kwargs['params'] = arguments
        elif arguments is not None:
            # Serializar copia para no mutar los argumentos originales
            kwargs['json'] = serialize_for_json(copy.deepcopy(arguments))
        res = function(url, **kwargs)
        if res.status_code != 200:
            message = res.text
            if res.status_code == 404:
                raise IDMissingInBackend(message)
            raise JobError(message)
        res.raise_for_status()
        return res.json()


class MagentoAPI(object):
    """Magento 2.0+ API wrapper"""

    def __init__(self, location):
        """
        :param location: Magento location
        :type location: :class:`MagentoLocation`
        """
        self._location = location
        self._api = None

    @property
    def api(self):
        if self._api is None:
            self._api = Magento2Client(
                self._location.location,
                self._location.token,
                self._location.verify_ssl,
                use_custom_api_path=self._location.use_custom_api_path
            )
        return self._api

    def __enter__(self):
        # we do nothing, api is lazy
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Magento2Client no tiene __exit__, no necesita cleanup
        pass

    def call(self, method, arguments, http_method=None, storeview=None):
        try:
            # When Magento is installed on PHP 5.4+, the API
            # may return garble data if the arguments contain
            # trailing None.
            if isinstance(arguments, list):
                while arguments and arguments[-1] is None:
                    arguments.pop()
            start = datetime.now()
            try:
                result = self.api.call(
                    method, arguments, http_method=http_method,
                    storeview=storeview)
            except Exception as e:
                _logger.error("api.call('%s', %s) failed", method, arguments)
                raise e
            else:
                _logger.debug("api.call('%s', %s) returned %s in %s seconds",
                              method, arguments, result,
                              (datetime.now() - start).seconds)
            return result
        except (socket.gaierror, socket.error, socket.timeout) as err:
            raise NetworkRetryableError(
                'A network error caused the failure of the job: '
                '%s' % err)
        except requests.exceptions.RequestException as err:
            # Capturar errores HTTP de requests
            raise RetryableJobError(
                'An HTTP error caused the failure of the job: %s' % err)


class MagentoCRUDAdapter(AbstractComponent):
    """ External Records Adapter for Magento 2.0+ """
    # pylint: disable=method-required-super

    _name = 'magento.crud.adapter'
    _inherit = ['base.backend.adapter', 'base.magento.connector']
    _usage = 'backend.adapter'

    def search(self, filters=None):
        """ Search records according to some criterias
        and returns a list of ids """
        raise NotImplementedError

    def read(self, external_id, attributes=None, storeview=None):
        """ Returns the information of a record """
        raise NotImplementedError

    def search_read(self, filters=None):
        """ Search records according to some criterias
        and returns their information"""
        raise NotImplementedError

    def create(self, data):
        """ Create a record on the external system """
        raise NotImplementedError

    def write(self, external_id, data):
        """ Update records on the external system """
        raise NotImplementedError

    def delete(self, external_id):
        """ Delete a record on the external system """
        raise NotImplementedError

    def _call(self, method, arguments=None,
              http_method=None, storeview=None):
        try:
            magento_api = getattr(self.work, 'magento_api')
        except AttributeError:
            raise AttributeError(
                'You must provide a magento_api attribute with a '
                'MagentoAPI instance to be able to use the '
                'Backend Adapter.'
            )
        return magento_api.call(
            method, arguments, http_method=http_method, storeview=storeview)


class GenericAdapter(AbstractComponent):
    """Generic Magento 2.0+ adapter"""
    # pylint: disable=method-required-super

    _name = 'magento.adapter'
    _inherit = 'magento.crud.adapter'

    _magento2_model = None
    _magento2_search = None
    _magento2_key = None
    _magento2_name = None
    _admin2_path = None

    @staticmethod
    def get_searchCriteria(filters):
        """ Craft Magento 2.0 searchCriteria from filters, for example:
        'searchCriteria[filter_groups][0][filters][0][field]': 'website_id',
        'searchCriteria[filter_groups][0][filters][0][value]': '1,2',
        'searchCriteria[filter_groups][0][filters][0][condition_type]': 'in',

        Presumably, filter_groups are joined with AND, while filters in the
        same group are joined with OR (not supported here).
        """
        filters = filters or {}
        res = {}
        count = 0
        expr = 'searchCriteria[filter_groups][%s][filters][0][%s]'
        # http://devdocs.magento.com/guides/v2.0/howdoi/webapi/search-criteria.html
        operators = [
            'eq', 'finset', 'from', 'gt', 'gteq', 'in', 'like', 'lt',
            'lteq', 'moreq', 'neq', 'nin', 'notnull', 'null', 'to']
        for field in filters.keys():
            for op in filters[field].keys():
                assert op in operators
                value = filters[field][op]
                if isinstance(value, (list, set)):
                    value = ','.join(value)
                res.update({
                    expr % (count, 'field'): field,
                    expr % (count, 'condition_type'): op,
                    expr % (count, 'value'): value,
                })
                count += 1
        _logger.debug('searchCriteria %s from %s', res, filters)
        return res if res else {'searchCriteria': ''}

    def search(self, filters=None):
        """ Search records according to some criterias
        and returns a list of unique identifiers

        Query the resource to return the key field for all records.
        Filter out the 0, which designates a magic value, such as the
        global scope for websites, store groups and store views, or
        the category for customers that have not yet logged in.

        /search APIs return a dictionary with a top level 'items' key.
        Repository APIs return a list of items.

        :rtype: list
        """
        key = self._magento2_key or 'id'
        params = {}
        if self._magento2_search:
            params['fields'] = 'items[%s]' % key
            params.update(self.get_searchCriteria(filters))
        else:
            params['fields'] = key
            if filters:
                raise NotImplementedError
        res = self._call(
            self._magento2_search or self._magento2_model,
            params)
        if 'items' in res:
            res = res['items'] or []
        return [item[key] for item in res if item[key] != 0]

    @staticmethod
    def escape(term):
        if isinstance(term, str):
            return quote_plus(term)
        return term

    def read(self, external_id, attributes=None, storeview=None, **kwargs):
        """ Returns the information of a record

        :rtype: dict
        """
        if self._magento2_key:
            return self._call(
                ('%s/%s' % (self._magento2_model, self.escape(external_id))) % kwargs,
                None, storeview=storeview)
        res = self._call(self._magento2_model % kwargs, None)
        return next(record for record in res if str(record['id']) == external_id)

    def search_read(self, filters=None):
        """ Search records according to some criterias
        and returns their information"""
        params = {}
        if self._magento2_search:
            params.update(self.get_searchCriteria(filters))
        else:
            if filters:
                raise NotImplementedError
        return self._call(
            self._magento2_search or self._magento2_model, params)

    def create(self, data, storeview=None, **kwargs):
        """ Create a record on the external system """
        if self._magento2_name:
            new_object = self._call(
                self._magento2_model % kwargs,
                {self._magento2_name: data,
                 'saveOptions': True}, http_method='post')
        else:
            new_object = self._call(
                self._magento2_model % kwargs,
                data, http_method='post')
        if isinstance(new_object, dict):
            data.update(new_object)
        return self._get_id_from_create(new_object, data)

    def _get_id_from_create(self, result, data=None):
        return result['id']

    def write(self, id, data, storeview=None, **kwargs):
        """ Update records on the external system """
        if self._magento2_name:
            return self._call(
                ('%s/%s' % (self._magento2_model, id)) % kwargs,
                {self._magento2_name: data}, http_method='put',
                storeview=storeview or 'all')
        else:
            return self._call(
                ('%s/%s' % (self._magento2_model, id)) % kwargs,
                data, http_method='put', storeview=storeview or 'all')

    def delete(self, external_id, **kwargs):
        """ Delete a record on the external system """
        res = self._call(
            '%s/%s' % (self._magento2_model, self.escape(external_id)),
            None, http_method="delete")
        _logger.info("Record %s deleted on Magento", external_id)
        return res

    def admin_url(self, external_id):
        """ Return the URL in the Magento admin for a record """
        backend = self.backend_record
        url = backend.admin_location
        if not url:
            raise ValueError('No admin URL configured on the backend.')
        if hasattr(self.model, '_get_admin_path'):
            admin_path = getattr(self.model, '_get_admin_path')(
                backend, external_id)
        else:
            admin_path = self._admin2_path
        if admin_path is None:
            raise ValueError('No admin path is defined for this record')
        path = admin_path.format(id=external_id)
        url = url.rstrip('/')
        path = path.lstrip('/')
        url = '/'.join((url, path))
        return url
