from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from oa_web import views

urlpatterns = [
    path("", views.papers, name="papers"),
    path("actions/", views.action_list, name="actions"),
    path("report/", views.report, name="report"),
    path("history/", views.history, name="history"),
    path("sync/", views.sync, name="sync"),
    path("paper/<str:pub_id>/", views.paper, name="paper"),
    path("paper/<str:pub_id>/do/", views.paper_action, name="paper_action"),
    path("paper/<str:pub_id>/draft/<str:filename>", views.draft, name="draft"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
