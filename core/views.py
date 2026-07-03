import json
from django.http import JsonResponse
from django.shortcuts import render, redirect
from .services import run_pricing


def index(request):
    is_auth = request.session.get('authenticated', False)
    return render(request, 'core/index.html', {'is_auth': is_auth})


def login_view(request):
    error = None
    if request.method == 'POST':
        password = request.POST.get('password')
        if password == '1995':
            request.session['authenticated'] = True
            next_url = request.POST.get('next', '/')
            return redirect(next_url)
        else:
            error = 'ACCESS DENIED: INVALID CLEARANCE CODE.'

    if request.session.get('authenticated'):
        return redirect('index')

    return render(request, 'core/calculator_auth.html', {'error': error})


def logout_view(request):
    request.session.flush()
    return redirect('index')


def options_calculator(request):
    is_auth = request.session.get('authenticated', False)
    return render(request, 'core/options.html', {'is_auth': is_auth})


def exotics_calculator(request):
    is_auth = request.session.get('authenticated', False)
    return render(request, 'core/exotics.html', {'is_auth': is_auth})


def elns_calculator(request):
    is_auth = request.session.get('authenticated', False)
    return render(request, 'core/elns.html', {'is_auth': is_auth})

def api_calculate(request):
    if not request.session.get('authenticated', False):
        return JsonResponse({"status": "error", "message": "Forbidden"}, status=403)
        
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)
        
    try:
        payload = json.loads(request.body)
        result = run_pricing(payload)
        return JsonResponse({"status": "success", "data": result})
    except ValueError as e:
        return JsonResponse({"status": "error", "error_type": "ValueError", "message": str(e)}, status=400)
    except Exception as e:
        error_type = e.__class__.__name__
        return JsonResponse({"status": "error", "error_type": error_type, "message": str(e)}, status=400)
