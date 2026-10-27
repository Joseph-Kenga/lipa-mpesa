"""Example URLs. Include in your project urls.py with:

    path("payments/", include("payments.urls")),

Avoid the words "mpesa" and "safaricom" in C2B URLs; Safaricom rejects them.
"""

from django.urls import path

from . import views

app_name = "mobile_pay"

urlpatterns = [
    path("stk/push/", views.stk_push, name="stk_push"),
    path("stk/callback/", views.stk_callback, name="stk_callback"),
    path("stk/status/<int:pk>/", views.stk_status, name="stk_status"),
    path("c2b/validate/", views.c2b_validate, name="c2b_validate"),
    path("c2b/confirm/", views.c2b_confirm, name="c2b_confirm"),
]
