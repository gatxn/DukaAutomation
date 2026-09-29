"""Role-based access: one Membership row per user, resolved to a Shop + role.

Every view that used to do `get_object_or_404(Shop, owner=request.user)` should
call `shop_for_user(request.user, min_role=...)` instead, which enforces the
role check and self-heals a missing Membership for pre-existing Shop.owner
rows (so no explicit migration step is a hard requirement for correctness).
"""
from django.http import Http404
from .models import Shop, Membership, ROLE_LEVEL, ROLE_OWNER, ROLE_AGENT

def get_membership(user):
    if not user or not user.is_authenticated:
        return None
    return Membership.objects.select_related('shop').filter(user=user).first()

def shop_for_user(user, min_role=ROLE_AGENT):
    membership = get_membership(user)
    if not membership:
        shop = Shop.objects.filter(owner=user).first()
        if not shop:
            raise Http404('No shop for this account.')
        membership = Membership.objects.create(shop=shop, user=user, role=ROLE_OWNER)
    if ROLE_LEVEL[membership.role] < ROLE_LEVEL[min_role]:
        raise Http404('Insufficient role for this action.')
    return membership.shop

def role_for(user):
    membership = get_membership(user)
    return membership.role if membership else None
