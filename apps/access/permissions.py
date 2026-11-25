ALL = "*"

PERMISSIONS = [
    "organizations.branch.manage",
    "access.role.manage",
    "access.user.invite",
    "accounting.journal.view",
    "accounting.journal.post",
    "accounting.period.close",
    "contacts.contact.view",
    "contacts.contact.manage",
    "sales.invoice.view",
    "sales.invoice.manage",
    "sales.invoice.post",
    "purchases.bill.view",
    "purchases.bill.manage",
    "purchases.bill.post",
    "properties.property.view",
    "properties.property.manage",
    "properties.lease.manage",
    "properties.payment.record",
    "workflows.approval.decide",
    "reports.report.view",
    "audit.log.view",
]

DEFAULT_ROLES = [
    ("Administrator", "Everything, including users, roles and settings", [ALL]),
    (
        "Manager",
        "Approves requests, views reports, manages properties and contacts",
        [
            "properties.property.view",
            "properties.property.manage",
            "properties.lease.manage",
            "properties.payment.record",
            "workflows.approval.decide",
            "reports.report.view",
            "accounting.journal.view",
            "audit.log.view",
            "contacts.contact.view",
            "contacts.contact.manage",
            "sales.invoice.view",
            "sales.invoice.manage",
            "purchases.bill.view",
            "purchases.bill.manage",
        ],
    ),
    (
        "Accountant",
        "Posts entries, closes periods, runs statements and views contacts",
        [
            "accounting.journal.view",
            "accounting.journal.post",
            "accounting.period.close",
            "reports.report.view",
            "properties.property.view",
            "contacts.contact.view",
            "sales.invoice.view",
            "sales.invoice.manage",
            "sales.invoice.post",
            "purchases.bill.view",
            "purchases.bill.manage",
            "purchases.bill.post",
        ],
    ),
    (
        "Property officer",
        "Day-to-day property work and contact management, no ledger posting",
        [
            "properties.property.view",
            "properties.property.manage",
            "properties.lease.manage",
            "properties.payment.record",
            "contacts.contact.view",
            "contacts.contact.manage",
        ],
    ),
    (
        "Employee",
        "General staff with read-only contact access",
        ["properties.property.view", "contacts.contact.view"],
    ),
    (
        "Auditor",
        "Read-only access to records, ledger, contacts and audit log",
        [
            "accounting.journal.view",
            "reports.report.view",
            "audit.log.view",
            "properties.property.view",
            "contacts.contact.view",
            "sales.invoice.view",
            "purchases.bill.view",
        ],
    ),
]

# External accounting firms must never receive company-administration powers
# through an accountant-client grant. Those remain with the client's own users.
ACCOUNTANT_GRANTABLE_PERMISSIONS = [
    code for code in PERMISSIONS
    if code not in {
        "organizations.branch.manage",
        "access.role.manage",
        "access.user.invite",
    }
]
