from django.urls import path

from . import views

urlpatterns = [
    path("sales-invoices/", views.SalesInvoiceListCreateView.as_view(), name="sales-invoice-list"),
    path("sales-invoices/<uuid:invoice_id>/", views.SalesInvoiceDetailView.as_view(), name="sales-invoice-detail"),
    path("purchase-bills/", views.PurchaseBillListCreateView.as_view(), name="purchase-bill-list"),
    path("purchase-bills/<uuid:bill_id>/", views.PurchaseBillDetailView.as_view(), name="purchase-bill-detail"),
]
