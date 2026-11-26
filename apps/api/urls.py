from django.urls import include, path

from . import property_views, views

urlpatterns = [
    path("me/", views.MeView.as_view()),
    path("roles/", views.RoleListView.as_view()),
    path("register/", views.RegisterView.as_view()),
    path("organizations/", views.CreateCompanyView.as_view()),
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
    path("invitations/", views.MemberInvitationListCreateView.as_view()),
    path("invitations/accept/", views.AcceptMemberInvitationView.as_view()),
    path(
        "invitations/<uuid:invitation_id>/revoke/",
        views.RevokeAccountantInvitationView.as_view(),
    ),
    path("periods/", views.PeriodListView.as_view()),
    path("periods/<uuid:period_id>/close/", views.ClosePeriodView.as_view()),
    path("periods/<uuid:period_id>/reopen/", views.ReopenPeriodView.as_view()),
    path("reports/trial-balance/", views.TrialBalanceView.as_view()),
    path("reports/income-statement/", views.IncomeStatementView.as_view()),
    path("properties/", property_views.PropertyListCreateView.as_view()),
    path("properties/<uuid:pk>/", property_views.PropertyDetailView.as_view()),
    path("properties/<uuid:property_id>/units/", property_views.PropertyUnitsView.as_view()),
    path("units/<uuid:pk>/", property_views.UnitDetailView.as_view()),
    path("", include("apps.contacts.urls")),
    path("", include("apps.sales.urls")),
]
