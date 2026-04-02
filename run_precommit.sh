#!/bin/bash
cd /home/nacho/git/odoo180/connector-magento
.venv/bin/pre-commit run -a > /tmp/precommit_full.txt 2>&1
echo "FINAL_EXIT=$?" >> /tmp/precommit_full.txt
touch /tmp/precommit_done
