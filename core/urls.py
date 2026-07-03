from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('options/', views.options_calculator, name='options'),
    path('exotics/', views.exotics_calculator, name='exotics'),
    path('elns/', views.elns_calculator, name='elns'),
    path('api/calculate/', views.api_calculate, name='api_calculate'),
]
