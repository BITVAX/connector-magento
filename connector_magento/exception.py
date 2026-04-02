# © 2013 Guewen Baconnier,Camptocamp SA,Akretion
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.queue_job.exception import JobError
from odoo.addons.connector.exception import RetryableJobError


class NothingToDoJob(JobError):
    """Job has nothing to do and can be silently skipped.

    Replaces the removed ``odoo.addons.queue_job.exception.NothingToDoJob``
    that existed up to queue_job 16.0 but was dropped in 18.0.
    """


class OrderImportRuleRetry(RetryableJobError):
    """The sale order import will be retried later."""
