from django.urls import path

from . import views

urlpatterns = [
    path("me/", views.MeView.as_view()),
    path("roles/", views.RoleListView.as_view()),
    path("register/", views.RegisterView.as_view()),
    path("login/", views.LoginView.as_view()),
    path("logout/", views.LogoutView.as_view()),
    path("accountant-invitations/", views.CreateAccountantInvitationView.as_view()),
    path("accountant-invitations/list/", views.AccountantInvitationListView.as_view()),
    path("accountant-access/", views.AccountantAccessGrantListView.as_view()),
    path("accountant-invitations/accept/", views.AcceptAccountantInvitationView.as_view()),
    path(
        "accountant-invitations/<uuid:invitation_id>/revoke/",
        views.RevokeAccountantInvitationView.as_view(),
    ),
    path(
        "accountant-access/<uuid:grant_id>/revoke/",
        views.RevokeAccountantAccessView.as_view(),
    ),
]
