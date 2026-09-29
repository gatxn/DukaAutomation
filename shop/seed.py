from .models import Product, Conversation, Message, Order

def seed_demo(shop):
    samples = [('Linen shirt · White',45000,18,'Amina Hassan','Naomba shati jeupe, size M.'),('Kitenge tote bag',28000,24,'Juma Ally','Is the kitenge bag available?'),('Leather sandals',55000,9,'Neema Joseph','Nashukuru, nitasubiri oda yangu.')]
    for name,price,stock,customer,question in samples:
        product = Product.objects.create(shop=shop,name=name,price=price,stock=stock)
        conversation = Conversation.objects.create(shop=shop,name=customer,preview=question,product=product)
        Message.objects.create(conversation=conversation,direction='in',body=question)
        Message.objects.create(conversation=conversation,direction='out',body=f'Karibu! {name}: TZS {price:,}. Ungependa kuagiza?')
        Order.objects.create(shop=shop,name=customer,item=name,total=price,status=['Paid','Pending payment','Ready for delivery'][len(Order.objects.filter(shop=shop))])
