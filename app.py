from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///ecommerce.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True)
    password = db.Column(db.String(150))
    name = db.Column(db.String(150))
    address = db.Column(db.Text)
    orders = db.relationship('Order', backref='user', lazy=True)
    cart_items = db.relationship('CartItem', backref='user', lazy=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    products = db.relationship('Product', backref='category', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200))
    description = db.Column(db.Text)
    price = db.Column(db.Float)
    image = db.Column(db.String(200))
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    stock = db.Column(db.Integer, default=10)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    total = db.Column(db.Float)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', lazy=True)

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer)
    price = db.Column(db.Float)

    product = db.relationship('Product')

class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer)
    product = db.relationship('Product', backref='cart_items')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def home():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search')
    category_id = request.args.get('category')
    sort = request.args.get('sort', 'name')

    query = Product.query.join(Category)

    if search:
        query = query.filter(Product.name.contains(search))
    if category_id:
        try:
            cid = int(category_id)
            query = query.filter(Product.category_id == cid)
        except ValueError:
            pass

    if sort == 'price_low':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_high':
        query = query.order_by(Product.price.desc())
    else:
        query = query.order_by(Product.name.asc())

    products = query.paginate(page=page, per_page=6, error_out=False)
    categories = Category.query.all()
    return render_template('home.html', products=products, categories=categories)

@app.route('/product/<int:id>')
def product(id):
    prod = Product.query.get_or_404(id)
    return render_template('product.html', product=prod)

@app.route('/cart', methods=['GET', 'POST'])
@login_required
def cart():
    if request.method == 'POST':
        # Update quantities
        if 'update' in request.form:
            for key, value in request.form.items():
                if key.startswith('quantity_'):
                    try:
                        product_id = int(key.split('_')[1])
                        quantity = int(value)
                    except (ValueError, IndexError):
                        continue
                    # sanitize quantity
                    quantity = max(1, quantity)
                    quantity = min(quantity, 100)  # upper bound
                    cart_item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()
                    if cart_item:
                        cart_item.quantity = quantity
            db.session.commit()
        # Remove item
        elif 'remove' in request.form:
            try:
                product_id = int(request.form['remove'])
                cart_item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()
                if cart_item:
                    db.session.delete(cart_item)
                    db.session.commit()
            except (ValueError, KeyError):
                pass

    cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
    total = sum(item.product.price * item.quantity for item in cart_items)
    return render_template('cart.html', cart_items=cart_items, total=total)

@app.route('/add_to_cart/<int:product_id>')
@login_required
def add_to_cart(product_id):
    prod = Product.query.get_or_404(product_id)
    cart_item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if cart_item:
        cart_item.quantity = min(cart_item.quantity + 1, 100)
    else:
        cart_item = CartItem(user_id=current_user.id, product_id=product_id, quantity=1)
        db.session.add(cart_item)
    db.session.commit()
    flash('Added to cart!')
    return redirect(url_for('home'))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if request.method == 'POST':
        cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
        if not cart_items:
            flash('Cart is empty!')
            return redirect(url_for('cart'))

        # Check stock availability first
        for item in cart_items:
            if item.product.stock < item.quantity:
                flash(f'Not enough stock for {item.product.name}. Available: {item.product.stock}')
                return redirect(url_for('cart'))

        order = Order(user_id=current_user.id, total=sum(item.product.price * item.quantity for item in cart_items))
        db.session.add(order)
        db.session.flush()

        for item in cart_items:
            order_item = OrderItem(order_id=order.id, product_id=item.product_id, quantity=item.quantity, price=item.product.price)
            db.session.add(order_item)
            # decrease stock
            item.product.stock = max(0, item.product.stock - item.quantity)
            db.session.delete(item)

        db.session.commit()
        flash('Order placed successfully!')
        return redirect(url_for('orders'))

    cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
    total = sum(item.product.price * item.quantity for item in cart_items)
    return render_template('checkout.html', cart_items=cart_items, total=total)

@app.route('/orders')
@login_required
def orders():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.date.desc()).all()
    return render_template('orders.html', orders=orders)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('home'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        name = request.form['name']
        address = request.form['address']
        if User.query.filter_by(email=email).first():
            flash('Email already registered')
            return render_template('register.html')
        user = User(email=email, password=password, name=name, address=address)
        db.session.add(user)
        db.session.commit()
        flash('Registered! Please login.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.name = request.form.get('name', current_user.name)
        current_user.address = request.form.get('address', current_user.address)
        db.session.commit()
        flash('Profile updated')
        return redirect(url_for('profile'))
    return render_template('profile.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Add demo data only if nothing exists
        if not Category.query.first():
            cats = [
                Category(name='Electronics'),
                Category(name='Fashion'),
                Category(name='Home'),
                Category(name='Sports')
            ]
            for cat in cats:
                db.session.add(cat)
            db.session.commit()

        if not Product.query.first():
            prods = [
                Product(name='iPhone 15', description='Latest Apple smartphone with A17 chip', price=999.99, image='https://via.placeholder.com/300x200?text=iPhone+15', category_id=1, stock=10),
                Product(name='Samsung 65\" 4K TV', description='Smart TV with QLED technology', price=799.99, image='https://via.placeholder.com/300x200?text=Samsung+TV', category_id=1, stock=5),
                Product(name='Nike Air Max 90', description='Classic running shoes', price=129.99, image='https://via.placeholder.com/300x200?text=Nike+Shoes', category_id=4, stock=20),
                Product(name='Adidas Originals Jacket', description='Cozy winter jacket', price=89.99, image='https://via.placeholder.com/300x200?text=Adidas+Jacket', category_id=2, stock=15),
                Product(name='Keurig Coffee Maker', description='Single-serve coffee machine', price=149.99, image='https://via.placeholder.com/300x200?text=Coffee+Maker', category_id=3, stock=8),
                Product(name='MacBook Pro 16\"', description='M3 Pro chip, 18GB RAM', price=2499.99, image='https://via.placeholder.com/300x200?text=MacBook', category_id=1, stock=3),
                Product(name=\"Levi's 501 Jeans\", description='Original straight fit', price=69.99, image='https://via.placeholder.com/300x200?text=Levi%27s+Jeans', category_id=2, stock=25),
                Product(name='Lululemon Yoga Mat', description='Eco-friendly non-slip mat', price=29.99, image='https://via.placeholder.com/300x200?text=Yoga+Mat', category_id=4, stock=30)
            ]
            for prod in prods:
                db.session.add(prod)
            db.session.commit()

        if not User.query.filter_by(email='demo@example.com').first():
            user = User(email='demo@example.com', password=generate_password_hash('123456'), name='Demo User', address='123 Demo St, City')
            db.session.add(user)
            db.session.commit()

    app.run(debug=True)
