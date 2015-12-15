# -*- coding: utf-8 -*-
#
#    Author: Damien Crier
#    Copyright 2015 Camptocamp SA
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from openerp import models, fields


class MagentoBackend(models.Model):
    _inherit = 'magento.backend'

    auto_bind_product = fields.Boolean(
        string='Auto Bind Product',
        default=False,
        help="Tic that box if you want to automatically export the"
             "product when it's available for sell (sale_ok is tic)"
        )
    default_mag_tax_id = fields.Many2one('magento.tax.class',
                                         string='Default tax')