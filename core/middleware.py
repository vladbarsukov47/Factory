from django.shortcuts import redirect
from django.urls import reverse


class AdminStaffOnlyMiddleware:
    """
    Если пользователь не staff — не пускаем в /admin/.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            user = request.user
            if user.is_authenticated and not user.is_staff:
                return redirect(reverse("core:work"))
        return self.get_response(request)
