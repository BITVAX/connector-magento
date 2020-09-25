# -*- coding: utf-8 -*-
# © 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{'name': 'Magento Connector Website Sale Glue Module',
 'version': '10.0.1.0.0',
 'category': 'Connector',
 'depends': ['connector_magento',
             'website_sale',
             'connector_magento_product_catalog',
             ],
 'author': "Callino,Odoo Community Association (OCA)",
 'license': 'AGPL-3',
 'website': 'http://www.odoo-magento-connector.com',
 'data': [
     'views/product_category_views.xml',
     'views/product_views.xml',
     'views/product_media.xml',
     'views/product_image.xml',
 ],
 'installable': True,
 'auto_install': True,
 'application': False,
 }
