from django.urls import path

from . import views

urlpatterns = [
    path("payments/", views.PaymentListCreateView.as_view(), name="payment-list"),
    path("payments/<uuid:payment_id>/post/", views.PaymentDetailView.as_view(), name="payment-post"),
    path("payments/<uuid:payment_id>/", views.PaymentDetailView.as_view(), name="payment-detail"),
]
