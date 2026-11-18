ALL = "*"

PERMISSIONS = [
    "organizations.branch.manage",
    "access.role.manage",
    "access.user.invite",
    "accounting.journal.view",
    "accounting.journal.post",
    "accounting.period.close",
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
        "Approves requests, views reports, manages properties",
        [
            "properties.property.view",
            "properties.property.manage",
            "properties.lease.manage",
            "properties.payment.record",
            "workflows.approval.decide",
            "reports.report.view",
            "accounting.journal.view",
            "audit.log.view",
        ],
    ),
    (
        "Accountant",
        "Posts entries, closes periods, runs statements",
        [
            "accounting.journal.view",
            "accounting.journal.post",
            "accounting.period.close",
            "reports.report.view",
            "properties.property.view",
        ],
    ),
    (
        "Property officer",
        "Day-to-day property work, no ledger posting",
        [
            "properties.property.view",
            "properties.property.manage",
            "properties.lease.manage",
            "properties.payment.record",
        ],
    ),
    ("Employee", "General staff", ["properties.property.view"]),
    (
        "Auditor",
        "Read-only access to records, ledger and audit log",
        [
            "accounting.journal.view",
            "reports.report.view",
            "audit.log.view",
            "properties.property.view",
        ],
    ),
]