# Contacts API

All endpoints are under `/api/v1/`. The API uses the authenticated Django session and requires an organization to be selected with the `X-Organization-ID` request header. The organization must be an active membership of the user or a client organization for which the user has an active accountant grant.

## Permissions

- `contacts.contact.view`: list and read contacts, roles and addresses.
- `contacts.contact.manage`: create, update, deactivate and manage contact roles and addresses.

The default Manager and Property officer roles can manage contacts. Accountant, Employee and Auditor roles have read-only contact access. Administrator retains all permissions.

Existing organizations created before these permissions were introduced may need their role permissions updated by an administrator in Django admin. New organizations receive the updated defaults.

## Contacts

### List and search

`GET /api/v1/contacts/`

Optional query parameters:

- `search`: searches display name, personal names, legal name, email, phone and tax ID.
- `contact_type`: `individual` or `organization`.
- `role`: customer, supplier, contractor, subcontractor, tenant, landlord, property_owner or other.
- `is_active`: `true` or `false`.
- `city`, `state`: address filters.
- `page`: page number.
- `page_size`: page size (default 25, maximum 100).

The response uses standard DRF page-number pagination: `count`, `next`, `previous` and `results`.

### Create

`POST /api/v1/contacts/`

Example:

```json
{
  "contact_type": "organization",
  "display_name": "Northstar Construction",
  "legal_name": "Northstar Construction Limited",
  "email": "accounts@example.com",
  "phone": "+2348000000000",
  "tax_id": "TIN-123"
}
```

### Retrieve and update

- `GET /api/v1/contacts/{contact_id}/`
- `PUT /api/v1/contacts/{contact_id}/`
- `PATCH /api/v1/contacts/{contact_id}/`

Contact responses include read-only `roles` and `addresses` collections. Manage these through their dedicated endpoints.

### Deactivate

`DELETE /api/v1/contacts/{contact_id}/`

This is a soft deactivation. It sets `is_active=false`; it does not delete the contact, roles or addresses. Inactive contacts remain retrievable and can be included in list results with `is_active=false`.

## Contact roles

- `GET /api/v1/contacts/{contact_id}/roles/`
- `POST /api/v1/contacts/{contact_id}/roles/` with `{"role":"supplier"}`
- `DELETE /api/v1/contacts/{contact_id}/roles/{role_id}/`

A contact may have multiple distinct roles. Duplicate role assignments are rejected.

## Contact addresses

- `GET /api/v1/contacts/{contact_id}/addresses/`
- `POST /api/v1/contacts/{contact_id}/addresses/`
- `PATCH /api/v1/contacts/{contact_id}/addresses/{address_id}/`
- `DELETE /api/v1/contacts/{contact_id}/addresses/{address_id}/`

Address fields: `address_type`, `street_address`, `address_line_2`, `city`, `state`, `country`, `postal_code`, `is_default`. Only one default address is allowed per contact.

## Tenant and admin behaviour

Every API query is scoped by the active organization. A contact ID from another organization returns 404 rather than exposing whether the record exists. Related roles and addresses are also constrained to the parent contact and active organization.

The Django admin uses the organization selected by the authenticated application session. A user with exactly one active membership is automatically scoped to it. A user with multiple memberships must first select an organization in the application; the admin will not guess which company's records should be shown.

## Error responses

- 400: invalid input, unsupported filter or duplicate role/default address.
- 401/403: missing authentication, missing organization context or insufficient permission.
- 404: contact or child record is not present in the active organization.

No contact operation automatically creates or posts an accounting journal entry.
