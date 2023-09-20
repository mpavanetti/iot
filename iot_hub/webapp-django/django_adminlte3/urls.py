"""django_adminlte3 URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/dev/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, re_path
from django.conf.urls import include
from django.views.generic.base import TemplateView

urlpatterns = [
    re_path(r'^$', TemplateView.as_view(template_name='adminlte/index.html')),
    re_path(r'^login/$', TemplateView.as_view(template_name='adminlte/login.html')),
    re_path(r'^example/$', TemplateView.as_view(template_name='adminlte/example.html')),
    path('admin/', admin.site.urls),
]
