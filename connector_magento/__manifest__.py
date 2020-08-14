# -*- coding: utf-8 -*-
# © 2013 Guewen Baconnier,Camptocamp SA,Akretion
# © 2016 Sodexis
# © 2019 Wolfgang Pichler, Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
# Removed              'product_multi_category' dependency

{'name': 'Magento Connector',
 'version': '12.0.1.0.0',
 'category': 'Connector',
 'depends': ['account',
             'base_technical_user',
             'product',
             'delivery',
             'sale_stock',
             'connector_ecommerce',
             ],
 'external_dependencies': {
     'python': ['magento'],
 },
 'author': "Camptocamp,Akretion,Sodexis,Callino,Odoo Community Association (OCA)",
 'license': 'AGPL-3',
 'website': 'http://www.odoo-magento-connector.com',
 'images': ['images/magento_backend.png',
            'images/jobs.png',
            'images/product_binding.png',
            'images/invoice_binding.png',
            'images/connector_magento.png',
            ],
 'data': ['data/connector_magento_data.xml',
          'data/res_partner_category.xml',
          'security/ir.model.access.csv',
          'views/magento_backend_views.xml',
          'views/account_tax.xml',
          'views/product_template.xml',
          'views/product_views.xml',
          'views/product_bundle.xml',
          'views/product_category_views.xml',
          'views/product_media.xml',
          'views/partner_views.xml',
          'views/invoice_views.xml',
          'views/sale_order_views.xml',
          'views/magento_product_attributes.xml',
          'views/connector_magento_menu.xml',
          'views/delivery_views.xml',
          'views/stock_views.xml',
          'views/stock_items.xml',
          'views/stock_warehouse.xml',
          'views/account_payment_views.xml',
          'views/account_payment_mode_views.xml',
          'views/magento_external_objects_menus.xml',
          'views/template.xml',
          'wizards/add_backend.xml',
          'views/attribute.xml',
          'views/product_sync.xml',
          ],
 'installable': True,
 'application': False,
 }
