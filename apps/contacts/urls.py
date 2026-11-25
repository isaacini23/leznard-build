from django.urls import path

from . import views

urlpatterns = [
    path("contacts/", views.ContactListCreateView.as_view(), name="contact-list"),
    path(
        "contacts/<uuid:contact_id>/",
        views.ContactDetailView.as_view(),
        name="contact-detail",
    ),
    path(
        "contacts/<uuid:contact_id>/roles/",
        views.ContactRoleListCreateView.as_view(),
        name="contact-role-list",
    ),
    path(
        "contacts/<uuid:contact_id>/roles/<uuid:role_id>/",
        views.ContactRoleDetailView.as_view(),
        name="contact-role-detail",
    ),
    path(
        "contacts/<uuid:contact_id>/addresses/",
        views.ContactAddressListCreateView.as_view(),
        name="contact-address-list",
    ),
    path(
        "contacts/<uuid:contact_id>/addresses/<uuid:address_id>/",
        views.ContactAddressDetailView.as_view(),
        name="contact-address-detail",
    ),
]
